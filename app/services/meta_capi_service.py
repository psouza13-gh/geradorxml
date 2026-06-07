"""
Meta Conversions API (CAPI) — server-side event tracking for Meta/Facebook Ads.

Sends conversion events (Lead, StartTrial, Purchase, ...) directly from our
backend to Meta's Graph API, so that signups and subscriptions that happen
without a client-side pixel firing (or that get blocked by ad-blockers /
browser privacy features) are still attributed correctly for ad optimization.

Security model — IMPORTANT:
  • The long-lived ACCESS TOKEN is a server-side secret. It is read ONLY from
    the META_CAPI_ACCESS_TOKEN environment variable and is NEVER stored in the
    database, returned to the frontend, or logged.
  • The Pixel/Dataset ID and feature toggles (which events to send, test event
    code) are NOT secrets — they're stored in `app_settings` (key='meta_capi')
    and are editable from the admin panel (/admin).
  • Personally identifiable user data (email, phone) is hashed with SHA-256
    (lowercased + trimmed) before being sent, per Meta's Conversions API spec
    — Meta never receives plaintext PII from us.

Docs: https://developers.facebook.com/docs/marketing-api/conversions-api
"""
import os
import time
import json
import hashlib
import urllib.request
import urllib.error
import urllib.parse

from app.services.db import execute

GRAPH_API_VERSION = "v21.0"
GRAPH_API_URL     = "https://graph.facebook.com/{version}/{pixel_id}/events"

SETTINGS_KEY = "meta_capi"

_DEFAULT_SETTINGS = {
    "enabled":         False,
    "pixel_id":        "",
    "test_event_code": "",
    "events":          {"lead": True, "trial": True, "purchase": True},
}


# ── Token (env var only — never persisted/exposed) ────────────────────────────

def _access_token() -> str:
    return os.environ.get("META_CAPI_ACCESS_TOKEN", "").strip()


def token_configured() -> bool:
    return bool(_access_token())


# ── Settings (DB-backed, admin-editable; no secrets here) ─────────────────────

def get_settings() -> dict:
    """Return the current Meta CAPI settings, merged with sane defaults."""
    row = execute(
        "SELECT value FROM app_settings WHERE key = %s",
        (SETTINGS_KEY,),
        fetch="one",
    )
    settings = dict(_DEFAULT_SETTINGS)
    if row and row.get("value"):
        stored = row["value"]
        if isinstance(stored, str):       # psycopg2 may return JSONB as str depending on registration
            try:
                stored = json.loads(stored)
            except Exception:
                stored = {}
        settings.update({k: v for k, v in stored.items() if k in _DEFAULT_SETTINGS})
        if isinstance(stored.get("events"), dict):
            settings["events"] = {**_DEFAULT_SETTINGS["events"], **stored["events"]}
    return settings


def save_settings(*, enabled=None, pixel_id=None, test_event_code=None, events=None) -> dict:
    """Update one or more settings fields (None = leave unchanged). Returns the new settings."""
    current = get_settings()
    if enabled is not None:
        current["enabled"] = bool(enabled)
    if pixel_id is not None:
        current["pixel_id"] = str(pixel_id).strip()
    if test_event_code is not None:
        current["test_event_code"] = str(test_event_code).strip()
    if isinstance(events, dict):
        current["events"] = {**current["events"], **{k: bool(v) for k, v in events.items() if k in current["events"]}}

    execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (%s, %s::jsonb, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """,
        (SETTINGS_KEY, json.dumps(current)),
    )
    return current


def is_active() -> bool:
    """True only if the integration is enabled AND fully configured (token + pixel)."""
    s = get_settings()
    return bool(s.get("enabled") and s.get("pixel_id") and token_configured())


# ── PII hashing (Meta CAPI spec: lowercase, trim, SHA-256 hex) ────────────────

def _sha256(value: str) -> str | None:
    v = (value or "").strip().lower()
    if not v:
        return None
    return hashlib.sha256(v.encode("utf-8")).hexdigest()


def _hash_email(email: str | None) -> str | None:
    return _sha256(email)


def _hash_phone(telefone: str | None) -> str | None:
    """Normalize to digits-only with Brazil country code (55) before hashing."""
    if not telefone:
        return None
    digits = "".join(c for c in telefone if c.isdigit())
    if not digits:
        return None
    if not digits.startswith("55"):
        digits = "55" + digits
    return _sha256(digits)


# ── Low-level send ─────────────────────────────────────────────────────────────

def _post_events(payload: dict, pixel_id: str, timeout: float = 6.0) -> dict:
    """POST a Conversions API payload. Returns {'ok': bool, 'status': int, 'body': dict|str}."""
    token = _access_token()
    base_url = GRAPH_API_URL.format(version=GRAPH_API_VERSION, pixel_id=pixel_id)
    full_url = f"{base_url}?access_token={urllib.parse.quote(token)}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        full_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            return {"ok": True, "status": resp.status, "body": parsed}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return {"ok": False, "status": e.code, "body": parsed}
    except Exception as e:
        return {"ok": False, "status": 0, "body": str(e)}


def send_event(
    event_name: str,
    *,
    email: str | None = None,
    telefone: str | None = None,
    event_id: str | None = None,
    event_source_url: str | None = None,
    client_ip: str | None = None,
    client_user_agent: str | None = None,
    fbp: str | None = None,
    fbc: str | None = None,
    value: float | None = None,
    currency: str = "BRL",
    custom_data: dict | None = None,
    use_test_event_code: bool = False,
) -> dict:
    """
    Send a single server-side event to Meta's Conversions API.

    Silently no-ops (returns {'ok': False, 'skipped': <reason>}) when the
    integration isn't enabled/configured — callers can fire-and-forget this
    from request handlers without affecting the main flow on failure.
    """
    settings = get_settings()
    pixel_id = settings.get("pixel_id") or ""

    if not settings.get("enabled"):
        return {"ok": False, "skipped": "disabled"}
    if not pixel_id:
        return {"ok": False, "skipped": "no_pixel_id"}
    if not token_configured():
        return {"ok": False, "skipped": "no_access_token"}

    user_data = {}
    em = _hash_email(email)
    ph = _hash_phone(telefone)
    if em:
        user_data["em"] = [em]
    if ph:
        user_data["ph"] = [ph]
    if client_ip:
        user_data["client_ip_address"] = client_ip
    if client_user_agent:
        user_data["client_user_agent"] = client_user_agent
    if fbp:
        user_data["fbp"] = fbp
    if fbc:
        user_data["fbc"] = fbc

    cdata = dict(custom_data or {})
    if value is not None:
        cdata.setdefault("value", round(float(value), 2))
        cdata.setdefault("currency", currency)

    event = {
        "event_name":       event_name,
        "event_time":       int(time.time()),
        "action_source":    "website",
        "user_data":        user_data,
    }
    if event_id:
        event["event_id"] = event_id
    if event_source_url:
        event["event_source_url"] = event_source_url
    if cdata:
        event["custom_data"] = cdata

    payload = {"data": [event]}
    test_code = (settings.get("test_event_code") or "").strip()
    if use_test_event_code and test_code:
        payload["test_event_code"] = test_code

    try:
        result = _post_events(payload, pixel_id)
    except Exception as e:
        return {"ok": False, "skipped": f"send_error: {e}"}
    return result


# ── High-level helpers (call these from the app — they check toggles) ─────────

def track_lead(*, user_id: str, email: str | None, telefone: str | None = None,
               event_source_url: str | None = None,
               client_ip: str | None = None, client_user_agent: str | None = None) -> dict:
    """Fire on account creation / registration (top-of-funnel signal for Meta Ads)."""
    settings = get_settings()
    if not settings["events"].get("lead", True):
        return {"ok": False, "skipped": "event_disabled"}
    return send_event(
        "Lead",
        email=email, telefone=telefone,
        event_id=f"lead-{user_id}",
        event_source_url=event_source_url,
        client_ip=client_ip, client_user_agent=client_user_agent,
    )


def track_trial_start(*, user_id: str, email: str | None, telefone: str | None = None,
                       event_source_url: str | None = None,
                       client_ip: str | None = None, client_user_agent: str | None = None) -> dict:
    """Fire when a free-trial account is activated (24h / 1 CNPJ)."""
    settings = get_settings()
    if not settings["events"].get("trial", True):
        return {"ok": False, "skipped": "event_disabled"}
    return send_event(
        "StartTrial",
        email=email, telefone=telefone,
        event_id=f"trial-{user_id}",
        event_source_url=event_source_url,
        client_ip=client_ip, client_user_agent=client_user_agent,
    )


def track_purchase(*, user_id: str, email: str | None, plano: str, value: float,
                    telefone: str | None = None, currency: str = "BRL",
                    event_id_suffix: str | None = None) -> dict:
    """Fire when a paid subscription is confirmed (ASAAS payment webhook)."""
    settings = get_settings()
    if not settings["events"].get("purchase", True):
        return {"ok": False, "skipped": "event_disabled"}
    suffix = event_id_suffix or str(int(time.time()))
    return send_event(
        "Purchase",
        email=email, telefone=telefone,
        event_id=f"purchase-{user_id}-{suffix}",
        value=value, currency=currency,
        custom_data={"content_name": f"Plano {plano.capitalize()}", "content_type": "subscription"},
    )


def send_test_event(*, email: str = "teste@geradorxml.com.br") -> dict:
    """Used by the admin 'Enviar evento de teste' button — always uses the test_event_code."""
    return send_event(
        "Lead",
        email=email,
        event_id=f"admin-test-{int(time.time())}",
        custom_data={"content_name": "Evento de teste — painel admin"},
        use_test_event_code=True,
    )

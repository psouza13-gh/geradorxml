"""
/api/public — small read-only endpoints safe to expose WITHOUT authentication.

  GET /api/public/meta-pixel    — returns the Meta Pixel ID + enabled flag so the
                                   client-side (browser) Meta Pixel snippet can be
                                   loaded dynamically from the public pages.
  GET /api/public/integrations  — returns the admin-configured <head>/<body>
                                   tracking snippets (GTM, Clarity, ...) that
                                   public/js/integrations.js injects on every
                                   public page.

Security note:
  The Pixel/Dataset ID is NOT a secret — it is always visible in the page
  source of any site that uses the Meta Pixel (it travels in every client-side
  `fbq('init', <id>)` call and every network request to facebook.net). The
  long-lived ACCESS TOKEN used for server-side Conversions API calls is never
  exposed here (or anywhere on the frontend) — it lives only in the
  META_CAPI_ACCESS_TOKEN environment variable (see app/services/meta_capi_service.py).
"""
import sys, os
from flask import Flask, jsonify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services import meta_capi_service as meta_capi
from app.services.db import execute

app = Flask(__name__)


@app.route("/api/public/meta-pixel", methods=["GET"])
def meta_pixel_config():
    try:
        settings = meta_capi.get_settings()
        return jsonify({
            "enabled":  bool(settings.get("enabled")),
            "pixel_id": settings.get("pixel_id") or "",
        })
    except Exception:
        # Never break page load because of this — just report "not configured".
        return jsonify({"enabled": False, "pixel_id": ""})


@app.route("/api/public/integrations", methods=["GET"])
def site_integrations():
    """Snippets de rastreamento configurados no /admin → Integrações.

    Não são segredos: qualquer site com GTM/Clarity expõe esses códigos no
    HTML servido a todos os visitantes. Cache curto na CDN da Vercel para não
    consultar o banco a cada pageview.
    """
    import json as _json
    head = body = ""
    try:
        row = execute("SELECT value FROM app_settings WHERE key = %s",
                      ("site_integrations",), fetch="one")
        if row and row.get("value"):
            value = row["value"]
            if isinstance(value, str):
                value = _json.loads(value)
            head = value.get("head_code") or ""
            body = value.get("body_code") or ""
    except Exception:
        pass  # nunca quebrar o carregamento da página por causa disso

    resp = jsonify({"head": head, "body": body})
    resp.headers["Cache-Control"] = "public, max-age=60, s-maxage=300, stale-while-revalidate=600"
    return resp

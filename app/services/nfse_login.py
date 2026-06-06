"""
Authentication via CPF/CNPJ + senha for the Sistema Nacional NFS-e portal.
Portal: https://www.nfse.gov.br/EmissorNacional/Login

Discovered by live inspection of the portal HTML/JS:
  Form fields  : Inscricao (CPF/CNPJ), Senha, __RequestVerificationToken (CSRF)
  Form action  : POST https://www.nfse.gov.br/EmissorNacional/Login
  Token endpoint (confirmed live): /EmissorNacional/Account/ObterToken  → HTTP 500 w/o session

Token acquisition strategies (tried in order):
  1. Inline JS in the post-login redirect page (window.accessToken / setItem)
  2. GET /EmissorNacional/Account/ObterToken  with session cookie
  3. POST /EmissorNacional/Account/ObterToken with session cookie
  4. Raw JWT pattern search in any response body
"""

import re
import requests
from typing import Callable

PORTAL_BASE = "https://www.nfse.gov.br"
LOGIN_URL   = f"{PORTAL_BASE}/EmissorNacional/Login"
TOKEN_URL   = f"{PORTAL_BASE}/EmissorNacional/Account/ObterToken"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def autenticar_login(
    inscricao: str,
    senha: str,
    log: Callable[[str], None] = print,
) -> requests.Session:
    """
    Authenticate with the NFS-e portal using CPF/CNPJ (Inscricao) + senha.

    Returns a requests.Session with Authorization: Bearer header set,
    ready to call the NFS-e distribution API.

    Raises RuntimeError with a descriptive Portuguese message on failure.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": _UA,
        "Accept-Language": "pt-BR,pt;q=0.9",
    })

    # ── Step 1: GET login page → extract CSRF token ───────────────────────────
    log("  Acessando portal NFS-e...")
    try:
        resp = session.get(
            LOGIN_URL, timeout=30,
            headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Não foi possível acessar o portal NFS-e: {e}")

    csrf = _extract_csrf(resp.text)
    if not csrf:
        raise RuntimeError(
            "Token CSRF não encontrado na página de login. "
            "O portal pode estar temporariamente indisponível."
        )

    # ── Step 2: POST credentials ──────────────────────────────────────────────
    log("  Enviando credenciais...")
    try:
        resp = session.post(
            LOGIN_URL,
            data={
                "__RequestVerificationToken": csrf,
                "Inscricao": inscricao,
                "Senha":     senha,
            },
            timeout=30,
            allow_redirects=True,
            headers={
                "Accept":       "text/html,application/xhtml+xml,*/*;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer":      LOGIN_URL,
            },
        )
    except requests.RequestException as e:
        raise RuntimeError(f"Erro ao enviar credenciais: {e}")

    # Detect login failure (server re-renders login page with error)
    if _is_login_page(resp.url, resp.text):
        err = _extract_error_message(resp.text)
        raise RuntimeError(
            f"Login inválido no portal NFS-e. "
            f"{err or 'Verifique o CPF/CNPJ e a senha.'}"
        )

    log("  Login aceito.")

    # ── Step 3: Optional token acquisition ────────────────────────────────────
    # Token is used only for REST API calls. The HTML scraping approach (which
    # is what we actually use for download) works with session cookies alone.
    # We try to get the token anyway, but do NOT block if it fails.
    token, diag = _acquire_token(session, resp)

    if token:
        log("  Token de acesso obtido.")
        # IMPORTANT: portal JS uses  headers:{Authorization: token}  — NO "Bearer" prefix.
        session.headers.update({
            "Accept":        "application/json",
            "Authorization": token,
        })
    else:
        log("  Sessão autenticada via cookie (download por varredura de páginas).")

    session.cert = None
    return session


# ── Token acquisition ─────────────────────────────────────────────────────────

def _acquire_token(
    session: requests.Session,
    post_resp: requests.Response,
) -> tuple[str | None, str]:
    """
    Try every known strategy to get the Bearer token after a successful login.
    Returns (token, diagnostic_log).
    """
    diag_lines = []

    # Strategy 1 — inline JS in the post-login page HTML
    token = _token_from_html(post_resp.text)
    if token:
        diag_lines.append("✓ Token encontrado no HTML do dashboard.")
        return token, "\n".join(diag_lines)
    diag_lines.append("• HTML do dashboard: sem token embutido.")

    # Strategy 2 — GET /Account/ObterToken
    try:
        r = session.get(
            TOKEN_URL, timeout=20,
            headers={"Accept": "application/json, text/plain, */*"},
            allow_redirects=True,
        )
        diag_lines.append(
            f"• GET ObterToken → HTTP {r.status_code} | "
            f"Content-Type: {r.headers.get('Content-Type','?')} | "
            f"Body(200): {r.text[:200]!r}"
        )
        if r.ok and not _is_login_page(r.url, r.text):
            token = _token_from_response(r)
            if token:
                diag_lines.append("✓ Token obtido via GET ObterToken.")
                return token, "\n".join(diag_lines)
    except requests.RequestException as e:
        diag_lines.append(f"• GET ObterToken erro: {e}")

    # Strategy 3 — POST /Account/ObterToken
    try:
        r = session.post(
            TOKEN_URL, timeout=20,
            headers={"Accept": "application/json, text/plain, */*"},
            allow_redirects=True,
        )
        diag_lines.append(
            f"• POST ObterToken → HTTP {r.status_code} | "
            f"Content-Type: {r.headers.get('Content-Type','?')} | "
            f"Body(200): {r.text[:200]!r}"
        )
        if r.ok and not _is_login_page(r.url, r.text):
            token = _token_from_response(r)
            if token:
                diag_lines.append("✓ Token obtido via POST ObterToken.")
                return token, "\n".join(diag_lines)
    except requests.RequestException as e:
        diag_lines.append(f"• POST ObterToken erro: {e}")

    # Strategy 4 — scrape the dashboard for any JWT in loaded scripts
    try:
        r = session.get(
            f"{PORTAL_BASE}/EmissorNacional/",
            timeout=20,
            headers={"Accept": "text/html,*/*;q=0.8"},
        )
        if r.ok and not _is_login_page(r.url, r.text):
            token = _token_from_html(r.text)
            diag_lines.append(
                f"• Dashboard HTML → HTTP {r.status_code} | token: {'sim' if token else 'não'}"
            )
            if token:
                return token, "\n".join(diag_lines)
    except requests.RequestException as e:
        diag_lines.append(f"• Dashboard HTML erro: {e}")

    return None, "\n".join(diag_lines)


# ── Parsers / helpers ─────────────────────────────────────────────────────────

def _extract_csrf(html: str) -> str | None:
    m = re.search(
        r'<input[^>]+name=["\']__RequestVerificationToken["\'][^>]+value=["\']([^"\']+)["\']',
        html, re.I,
    )
    if m:
        return m.group(1)
    m = re.search(
        r'<input[^>]+value=["\']([^"\']+)["\'][^>]+name=["\']__RequestVerificationToken["\']',
        html, re.I,
    )
    return m.group(1) if m else None


def _is_login_page(url: str, html: str) -> bool:
    if "EmissorNacional/Login" in url:
        return True
    if 'name="Inscricao"' in html or 'placeholder="CPF/CNPJ"' in html:
        return True
    return False


def _extract_error_message(html: str) -> str:
    for pat in (
        r'<div[^>]+class=["\'][^"\']*validation-summary-errors[^"\']*["\'][^>]*>(.*?)</div>',
        r'<span[^>]+class=["\'][^"\']*field-validation-error[^"\']*["\'][^>]*>(.*?)</span>',
        r'<div[^>]+class=["\'][^"\']*alert[^"\']*["\'][^>]*>(.*?)</div>',
        r'<p[^>]+class=["\'][^"\']*text-danger[^"\']*["\'][^>]*>(.*?)</p>',
    ):
        m = re.search(pat, html, re.I | re.DOTALL)
        if m:
            text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            if text:
                return text
    return ""


def _token_from_html(html: str) -> str | None:
    """Search HTML/inline-JS for an embedded access token."""
    patterns = [
        # sessionStorage.setItem("accessToken","<token>")
        r'setItem\s*\(\s*["\']accessToken["\'],\s*["\']([^"\']{20,})["\']',
        # window.accessToken = "<token>"
        r'window\.accessToken\s*=\s*["\']([^"\']{20,})["\']',
        # "accessToken": "<token>"  (JSON blob in page)
        r'"accessToken"\s*:\s*"([^"]{20,})"',
        # "access_token": "<token>"
        r'"access_token"\s*:\s*"([^"]{20,})"',
        # any JWT (eyJ...) embedded in the page
        r'["\']?(eyJ[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,})["\']?',
    ]
    for p in patterns:
        m = re.search(p, html)
        if m:
            return m.group(1)
    return None


def _token_from_response(resp: requests.Response) -> str | None:
    """Extract token from an HTTP response (JSON or plain text)."""
    # Try JSON
    try:
        data = resp.json()
        if isinstance(data, str) and len(data) > 20:
            return data          # plain string response
        if isinstance(data, dict):
            for k in ("access_token", "accessToken", "token", "Token",
                      "jwt", "bearerToken", "BearerToken", "resultado"):
                v = data.get(k)
                if isinstance(v, str) and len(v) > 20:
                    return v
            # Nested
            for wrapper in ("data", "result", "payload", "dados"):
                sub = data.get(wrapper)
                if isinstance(sub, dict):
                    for k in ("access_token", "accessToken", "token"):
                        v = sub.get(k)
                        if isinstance(v, str) and len(v) > 20:
                            return v
    except Exception:
        pass

    # Plain text — if it's a compact string with no whitespace
    text = resp.text.strip()
    if 20 < len(text) < 2000 and ' ' not in text and '\n' not in text:
        return text

    # JWT pattern anywhere in body
    m = re.search(
        r'(eyJ[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,})',
        resp.text,
    )
    if m:
        return m.group(1)

    return None

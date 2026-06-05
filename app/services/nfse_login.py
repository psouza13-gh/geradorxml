"""
Authentication via e-mail + password for the Sistema Nacional NFS-e portal.
Portal URL: https://www.nfse.gov.br/EmissorNacional/Login

Flow:
  1. GET the login page → extract CSRF token / form details
  2. POST credentials (email + senha)
  3. Capture the Bearer token from the response (cookie, JSON body, or embedded JS)
  4. Return a requests.Session ready to call adn.nfse.gov.br

If authentication fails for any reason, raises RuntimeError with a
user-friendly Portuguese message.
"""

import re
import json
import requests
from html.parser import HTMLParser
from typing import Callable

PORTAL_BASE   = "https://www.nfse.gov.br"
PORTAL_LOGIN  = f"{PORTAL_BASE}/EmissorNacional/Login"

# Candidate REST endpoints that SPAs commonly use for token-based login
_API_LOGIN_CANDIDATES = [
    "/EmissorNacional/api/auth/login",
    "/EmissorNacional/api/autenticacao/login",
    "/EmissorNacional/api/usuario/login",
    "/EmissorNacional/api/login",
    "/api/auth/login",
    "/api/login",
]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── Minimal HTML form extractor ───────────────────────────────────────────────

class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms: list[dict] = []
        self._cur: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._cur = {"action": a.get("action", ""), "fields": {}}
        elif tag == "input" and self._cur is not None:
            name = a.get("name")
            if name:
                self._cur["fields"][name] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form" and self._cur is not None:
            self.forms.append(self._cur)
            self._cur = None


def _abs(base: str, href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        from urllib.parse import urlparse
        p = urlparse(base)
        return f"{p.scheme}://{p.netloc}{href}"
    return base.rstrip("/") + "/" + href


# ── Main authentication function ──────────────────────────────────────────────

def autenticar_login(
    email: str,
    senha: str,
    log: Callable[[str], None] = print,
) -> requests.Session:
    """
    Authenticate with the NFS-e portal (nfse.gov.br) using e-mail + senha.

    Returns a requests.Session with the Bearer token set in
    the Authorization header, ready to call the NFS-e distribution API.

    Raises RuntimeError with a descriptive message on failure.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin": PORTAL_BASE,
        "Referer": PORTAL_LOGIN,
    })

    # ── Strategy A: JSON API login (modern SPA pattern) ───────────────────────
    log("  Autenticando no portal NFS-e...")
    token = _try_api_login(session, email, senha, log)

    # ── Strategy B: HTML form login (fallback) ────────────────────────────────
    if not token:
        token = _try_form_login(session, email, senha, log)

    if not token:
        raise RuntimeError(
            "Não foi possível autenticar no portal NFS-e com as credenciais fornecidas.\n"
            "Verifique o e-mail e a senha e tente novamente.\n"
            "Se o portal exigir autenticação em dois fatores, use o Certificado Digital A1."
        )

    log("  Autenticado no portal NFS-e com sucesso.")
    session.headers.update({
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    })
    session.cert = None
    return session


# ── Strategy A: direct JSON API ───────────────────────────────────────────────

def _try_api_login(
    session: requests.Session,
    email: str,
    senha: str,
    log: Callable,
) -> str | None:
    """Try common JSON API login endpoints used by SPAs."""
    payloads = [
        {"email": email, "senha": senha},
        {"email": email, "password": senha},
        {"login": email, "senha": senha},
        {"login": email, "password": senha},
        {"username": email, "password": senha},
    ]

    for path in _API_LOGIN_CANDIDATES:
        url = PORTAL_BASE + path
        for payload in payloads:
            try:
                resp = session.post(
                    url,
                    json=payload,
                    timeout=20,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code in (200, 201):
                    token = _extract_token_from_json(resp)
                    if token:
                        return token
                elif resp.status_code in (401, 403):
                    # Server responded — endpoint exists but creds wrong or
                    # payload format mismatch; try next payload
                    body = _safe_json(resp)
                    msg = (body.get("message") or body.get("erro") or
                           body.get("error") or "")
                    if msg and any(s in msg.lower() for s in
                                   ("senha", "usuário", "inválid", "incorret", "credencial")):
                        raise RuntimeError(
                            f"E-mail ou senha incorretos no portal NFS-e: {msg}"
                        )
            except RuntimeError:
                raise
            except requests.RequestException:
                continue

    return None


# ── Strategy B: HTML form login ───────────────────────────────────────────────

def _try_form_login(
    session: requests.Session,
    email: str,
    senha: str,
    log: Callable,
) -> str | None:
    """Fall back to HTML form submission."""
    try:
        resp = session.get(PORTAL_LOGIN, timeout=30, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Não foi possível acessar o portal NFS-e: {e}")

    # Parse form
    parser = _FormParser()
    parser.feed(resp.text)

    login_form = None
    for form in parser.forms:
        keys = set(form["fields"])
        if any(k in keys for k in ("email", "login", "username", "j_username")):
            login_form = form
            break
    if not login_form:
        for form in parser.forms:
            if any(k in set(form["fields"]) for k in ("senha", "password", "j_password")):
                login_form = form
                break

    if not login_form:
        return None

    # Fill form
    fields = login_form["fields"].copy()
    for k in ("email", "login", "username", "j_username"):
        if k in fields:
            fields[k] = email
            break
    for k in ("senha", "password", "j_password", "pass"):
        if k in fields:
            fields[k] = senha
            break

    action = _abs(resp.url, login_form["action"] or resp.url)

    try:
        resp2 = session.post(
            action,
            data=fields,
            timeout=30,
            allow_redirects=True,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except requests.RequestException as e:
        raise RuntimeError(f"Erro ao enviar credenciais: {e}")

    # Check for wrong password indicators
    low = resp2.text.lower()
    if any(s in low for s in ("senha incorreta", "usuário não encontrado",
                               "credenciais inválidas", "login inválido")):
        raise RuntimeError("E-mail ou senha incorretos no portal NFS-e.")

    # Try to extract token from the response
    token = _extract_token_from_response(session, resp2)
    return token


# ── Token extraction helpers ─────────────────────────────────────────────────

def _extract_token_from_json(resp: requests.Response) -> str | None:
    data = _safe_json(resp)
    for k in ("access_token", "accessToken", "token", "id_token",
               "idToken", "jwt", "bearerToken"):
        v = data.get(k)
        if isinstance(v, str) and len(v) > 20:
            return v
    # Nested: data.token, data.data.access_token …
    for nested_key in ("data", "result", "payload"):
        sub = data.get(nested_key)
        if isinstance(sub, dict):
            for k in ("access_token", "accessToken", "token", "jwt"):
                v = sub.get(k)
                if isinstance(v, str) and len(v) > 20:
                    return v
    return None


def _extract_token_from_response(
    session: requests.Session,
    resp: requests.Response,
) -> str | None:
    # 1. JSON body
    token = _extract_token_from_json(resp)
    if token:
        return token

    # 2. HttpOnly cookie with token-like name
    for cookie in session.cookies:
        if cookie.name.lower() in ("access_token", "jwt", "token",
                                    "id_token", "auth_token", "bearer"):
            return cookie.value

    # 3. JWT pattern embedded in HTML/JS
    m = re.search(
        r'(?:access_token|token|jwt)\s*[=:]\s*["\']'
        r'(eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)["\']',
        resp.text,
    )
    if m:
        return m.group(1)

    # 4. Any bearer-looking string in JSON-like script tags
    m = re.search(
        r'"(?:access_token|token|bearerToken)"\s*:\s*"([A-Za-z0-9\-_.+/]{40,})"',
        resp.text,
    )
    if m:
        return m.group(1)

    return None


def _safe_json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {}

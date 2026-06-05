"""
Gov.br OAuth2 authentication for Sistema Nacional NFS-e.

Simulates the Authorization Code flow entirely via HTTP (no browser needed):
  1. GET NFS-e portal login page → extract Gov.br authorize URL
  2. GET Gov.br authorize → login form
  3. POST credentials → follow redirects back to NFS-e portal
  4. Capture Bearer token from cookie / API response

If the token cannot be captured (CAPTCHA, 2FA, layout change), raises
RuntimeError with a user-friendly message.
"""

import re
import json
import requests
from html.parser import HTMLParser
from typing import Callable

NFSE_LOGIN_URL = "https://www.nfse.gov.br/EmissorNacional/Login"
GOVBR_SSO_BASE = "https://sso.acesso.gov.br"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── HTML form parser ──────────────────────────────────────────────────────────

class _FormParser(HTMLParser):
    """Light HTML parser to extract <form> action and <input> defaults."""

    def __init__(self):
        super().__init__()
        self.forms: list[dict] = []
        self._cur: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._cur = {"action": a.get("action", ""), "method": a.get("method", "post").upper(), "fields": {}}
        elif tag == "input" and self._cur is not None:
            name = a.get("name")
            if name:
                self._cur["fields"][name] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form" and self._cur is not None:
            self.forms.append(self._cur)
            self._cur = None


def _abs_url(base: str, href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        from urllib.parse import urlparse
        p = urlparse(base)
        return f"{p.scheme}://{p.netloc}{href}"
    return base.rstrip("/") + "/" + href


# ── Main authentication function ──────────────────────────────────────────────

def autenticar_login(
    login: str,
    senha: str,
    log: Callable[[str], None] = print,
) -> requests.Session:
    """
    Authenticate with Gov.br credentials and return a requests.Session
    with the Bearer token set in the Authorization header.

    Parameters
    ----------
    login : CPF (11 digits, with or without formatting) or e-mail registered
            on Gov.br.
    senha : Gov.br account password.
    log   : logging callback (same as NfseNacionalClient).

    Returns
    -------
    requests.Session ready to call the NFS-e distribution API.

    Raises
    ------
    RuntimeError with a descriptive Portuguese message on any failure.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
    })

    # ── Step 1: NFS-e portal login page ──────────────────────────────────────
    log("  Acessando portal NFS-e...")
    try:
        resp = session.get(NFSE_LOGIN_URL, timeout=30, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Não foi possível acessar o portal NFS-e: {e}")

    # Find Gov.br authorize link in the page
    auth_url = _extract_govbr_auth_url(resp.text, resp.url)
    if not auth_url:
        raise RuntimeError(
            "Link de autenticação Gov.br não encontrado no portal NFS-e. "
            "O layout do portal pode ter mudado. "
            "Use o certificado digital como alternativa."
        )

    # ── Step 2: Gov.br authorize → login form ────────────────────────────────
    log("  Redirecionando para Gov.br...")
    try:
        resp = session.get(auth_url, timeout=30, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Erro ao acessar Gov.br: {e}")

    login_form = _find_login_form(resp.text)
    if not login_form:
        _check_for_blocking_conditions(resp.text)
        raise RuntimeError(
            "Formulário de login Gov.br não encontrado. "
            "O portal pode estar exigindo CAPTCHA, autenticação em dois fatores "
            "ou o layout da página mudou. Use o certificado digital como alternativa."
        )

    # ── Step 3: POST credentials ──────────────────────────────────────────────
    log("  Enviando credenciais...")
    fields = login_form["fields"].copy()

    # Fill username field (Gov.br uses various field names)
    for k in ("username", "j_username", "login", "cpf", "user"):
        if k in fields:
            fields[k] = login
            break
    else:
        # Best-effort: set any field that looks like a login
        for k in list(fields):
            if any(s in k.lower() for s in ("user", "login", "cpf", "email")):
                fields[k] = login
                break

    # Fill password field
    for k in ("password", "j_password", "senha", "pass"):
        if k in fields:
            fields[k] = senha
            break
    else:
        for k in list(fields):
            if any(s in k.lower() for s in ("pass", "senha", "pwd")):
                fields[k] = senha
                break

    action = _abs_url(resp.url, login_form["action"] or resp.url)

    try:
        resp = session.post(
            action,
            data=fields,
            timeout=30,
            allow_redirects=True,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Referer": resp.url},
        )
    except requests.RequestException as e:
        raise RuntimeError(f"Erro ao enviar credenciais para Gov.br: {e}")

    # ── Check for common failure modes ────────────────────────────────────────
    _check_for_blocking_conditions(resp.text)

    body_lower = resp.text.lower()
    if any(s in body_lower for s in ("incorreta", "inválid", "inválida", "incorrect",
                                      "login ou senha", "senha incorreta")):
        raise RuntimeError("Login ou senha Gov.br incorretos. Verifique as credenciais.")

    # ── Step 4: Extract Bearer token ─────────────────────────────────────────
    log("  Autenticação realizada. Capturando token...")
    token = _extract_bearer_token(session, resp)
    if not token:
        raise RuntimeError(
            "Autenticação realizada com Gov.br, mas não foi possível capturar o token "
            "de acesso à API NFS-e. Tente novamente ou use o certificado digital."
        )

    log("  Autenticado via Gov.br com sucesso.")
    session.headers.update({
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    })
    # Remove mTLS cert if any was set
    session.cert = None
    return session


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_govbr_auth_url(html: str, page_url: str) -> str | None:
    """Find the Gov.br authorize URL from the NFS-e login page."""
    # Direct href to Gov.br SSO
    m = re.search(
        r'href=["\']?(https://sso\.acesso\.gov\.br/authorize[^"\'>\s]+)',
        html, re.I
    )
    if m:
        return m.group(1).replace("&amp;", "&")

    # Form action pointing to Gov.br
    m = re.search(
        r'action=["\']?(https://sso\.acesso\.gov\.br[^"\'>\s]+)',
        html, re.I
    )
    if m:
        return m.group(1).replace("&amp;", "&")

    # JS redirect: window.location = "..."
    m = re.search(
        r'window\.location(?:\.href)?\s*=\s*["\']'
        r'(https://sso\.acesso\.gov\.br[^"\']+)["\']',
        html, re.I
    )
    if m:
        return m.group(1)

    return None


def _find_login_form(html: str) -> dict | None:
    """Return the login form dict, or None if not found."""
    parser = _FormParser()
    parser.feed(html)
    # Look for form with username/password fields
    for form in parser.forms:
        keys = set(form["fields"])
        if any(k in keys for k in ("username", "j_username", "login", "cpf", "user")):
            return form
    # Fallback: any form with a password field
    for form in parser.forms:
        keys = set(form["fields"])
        if any(k in keys for k in ("password", "j_password", "senha")):
            return form
    return None


def _check_for_blocking_conditions(html: str) -> None:
    """Raise RuntimeError for known blocking situations."""
    lower = html.lower()
    if "captcha" in lower:
        raise RuntimeError(
            "CAPTCHA detectado na página Gov.br. "
            "Acesse o portal manualmente uma vez para resolver o CAPTCHA, "
            "depois tente novamente — ou use o certificado digital."
        )
    if any(s in lower for s in ("dois fatores", "two-factor", "2fa",
                                 "código de verificação", "código enviado")):
        raise RuntimeError(
            "Autenticação em dois fatores (2FA) ativada na conta Gov.br. "
            "Desative o 2FA temporariamente nas configurações do Gov.br, "
            "ou use o certificado digital A1 como método de autenticação."
        )


def _extract_bearer_token(session: requests.Session, last_resp: requests.Response) -> str | None:
    """
    Try several strategies to extract the Bearer token after successful Gov.br login.
    Returns the token string or None.
    """
    # Strategy 1: look in session cookies
    for cookie in session.cookies:
        if cookie.name.lower() in ("access_token", "jwt", "token", "id_token",
                                    "bearer", "auth_token"):
            return cookie.value

    # Strategy 2: JSON in response body
    try:
        data = last_resp.json()
        for k in ("access_token", "token", "id_token", "jwt"):
            if k in data:
                return data[k]
    except (ValueError, AttributeError):
        pass

    # Strategy 3: token embedded in HTML (script tag)
    for pattern in (
        r'"access_token"\s*:\s*"([^"]{20,})"',
        r'"token"\s*:\s*"([^"]{20,})"',
        r'Bearer\s+([A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)',  # JWT pattern
        r'localStorage\.setItem\(["\']token["\'],\s*["\']([^"\']+)["\']',
    ):
        m = re.search(pattern, last_resp.text)
        if m:
            return m.group(1)

    # Strategy 4: call the portal's token endpoint
    for token_path in (
        "/EmissorNacional/api/token",
        "/EmissorNacional/api/auth/token",
        "/api/token",
        "/auth/token",
    ):
        try:
            r = session.get(
                f"https://www.nfse.gov.br{token_path}",
                timeout=10,
                headers={"Accept": "application/json"},
            )
            if r.ok:
                d = r.json()
                for k in ("access_token", "token", "id_token"):
                    if k in d:
                        return d[k]
        except Exception:
            continue

    return None

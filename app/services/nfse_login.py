"""
Authentication via CPF/CNPJ + senha for the Sistema Nacional NFS-e portal.
Portal: https://www.nfse.gov.br/EmissorNacional/Login

Discovered flow (by inspecting the portal HTML/JS):
  1. GET /EmissorNacional/Login
       → extracts __RequestVerificationToken (ASP.NET anti-forgery)
  2. POST /EmissorNacional/Login
       fields: Inscricao=<CPF/CNPJ>, Senha=<senha>, __RequestVerificationToken
       → server sets auth session cookie, redirects to dashboard
  3. GET /EmissorNacional/Account/ObterToken  (with session cookie)
       → returns the Bearer token used by the portal JS (sessionStorage["accessToken"])
  4. Use token in Authorization: Bearer <token> for adn.nfse.gov.br API calls
"""

import re
import json
import requests
from typing import Callable

PORTAL_BASE  = "https://www.nfse.gov.br"
LOGIN_URL    = f"{PORTAL_BASE}/EmissorNacional/Login"
TOKEN_URL    = f"{PORTAL_BASE}/EmissorNacional/Account/ObterToken"

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
    Authenticate with the NFS-e portal using CPF/CNPJ + senha.

    Parameters
    ----------
    inscricao : CPF (11 digits) or CNPJ (14 digits), with or without
                formatting (dots/slashes/dashes are stripped automatically).
    senha     : portal password.
    log       : logging callback.

    Returns
    -------
    requests.Session with Authorization: Bearer header set, ready to call
    the NFS-e distribution API (adn.nfse.gov.br).

    Raises
    ------
    RuntimeError with a descriptive Portuguese message on failure.
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
            LOGIN_URL,
            timeout=30,
            headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Não foi possível acessar o portal NFS-e: {e}")

    csrf = _extract_csrf(resp.text)
    if not csrf:
        raise RuntimeError(
            "Token CSRF não encontrado na página de login do portal NFS-e. "
            "O portal pode estar temporariamente indisponível."
        )

    # ── Step 2: POST credentials ──────────────────────────────────────────────
    log("  Enviando credenciais...")
    payload = {
        "__RequestVerificationToken": csrf,
        "Inscricao": inscricao,
        "Senha": senha,
    }
    try:
        resp = session.post(
            LOGIN_URL,
            data=payload,
            timeout=30,
            allow_redirects=True,
            headers={
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": LOGIN_URL,
            },
        )
    except requests.RequestException as e:
        raise RuntimeError(f"Erro ao enviar credenciais: {e}")

    # Detect login failure (server returns the login page again with error msg)
    if _is_login_page(resp.url, resp.text):
        # Try to extract specific error from the page
        msg = _extract_error_message(resp.text)
        raise RuntimeError(
            f"Login inválido no portal NFS-e. {msg or 'Verifique o CPF/CNPJ e a senha.'}"
        )

    # ── Step 3: GET Bearer token ──────────────────────────────────────────────
    log("  Obtendo token de acesso...")
    token = _get_bearer_token(session)
    if not token:
        raise RuntimeError(
            "Autenticação realizada no portal, mas não foi possível obter o token "
            "de acesso à API. Tente novamente ou use o Certificado Digital A1."
        )

    log("  Autenticado no portal NFS-e com sucesso.")
    session.headers.update({
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    })
    session.cert = None
    return session


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_csrf(html: str) -> str | None:
    """Extract __RequestVerificationToken from the login form."""
    m = re.search(
        r'<input[^>]+name=["\']__RequestVerificationToken["\'][^>]+value=["\']([^"\']+)["\']',
        html, re.I,
    )
    if m:
        return m.group(1)
    # Alternative attribute order
    m = re.search(
        r'<input[^>]+value=["\']([^"\']+)["\'][^>]+name=["\']__RequestVerificationToken["\']',
        html, re.I,
    )
    return m.group(1) if m else None


def _is_login_page(url: str, html: str) -> bool:
    """Return True if the response looks like the login page (meaning login failed)."""
    if "EmissorNacional/Login" in url:
        return True
    # Also detect if the body still contains the login form
    if 'name="Inscricao"' in html or 'name="Senha"' in html:
        return True
    return False


def _extract_error_message(html: str) -> str:
    """Try to extract a server-side validation error from the login page HTML."""
    # ASP.NET MVC typically uses .validation-summary-errors or .field-validation-error
    patterns = [
        r'<div[^>]+class=["\'][^"\']*validation-summary-errors[^"\']*["\'][^>]*>(.*?)</div>',
        r'<span[^>]+class=["\'][^"\']*field-validation-error[^"\']*["\'][^>]*>(.*?)</span>',
        r'<div[^>]+class=["\'][^"\']*alert[^"\']*["\'][^>]*>(.*?)</div>',
        r'<p[^>]+class=["\'][^"\']*text-danger[^"\']*["\'][^>]*>(.*?)</p>',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.I | re.DOTALL)
        if m:
            # Strip inner tags
            text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            if text:
                return text
    return ""


def _get_bearer_token(session: requests.Session) -> str | None:
    """
    Call the portal's token endpoint (discovered by inspecting the portal JS)
    and return the Bearer token string.
    """
    try:
        resp = session.get(
            TOKEN_URL,
            timeout=20,
            headers={"Accept": "application/json, text/plain, */*"},
        )
        if not resp.ok:
            return None

        # Try JSON response first
        try:
            data = resp.json()
            # Various possible key names
            for k in ("access_token", "accessToken", "token", "Token",
                      "jwt", "bearerToken", "BearerToken"):
                v = data.get(k) if isinstance(data, dict) else None
                if isinstance(v, str) and len(v) > 20:
                    return v
            # Nested under "data" or "result"
            for wrapper in ("data", "result", "payload"):
                sub = data.get(wrapper) if isinstance(data, dict) else None
                if isinstance(sub, dict):
                    for k in ("access_token", "accessToken", "token"):
                        v = sub.get(k)
                        if isinstance(v, str) and len(v) > 20:
                            return v
        except ValueError:
            pass

        # Plain-text response (some endpoints return the token as raw string)
        text = resp.text.strip()
        if len(text) > 20 and '\n' not in text and ' ' not in text:
            return text

        # JWT embedded anywhere in the response
        m = re.search(
            r'(eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)',
            resp.text,
        )
        if m:
            return m.group(1)

    except requests.RequestException:
        pass

    return None

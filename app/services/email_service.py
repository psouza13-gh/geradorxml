"""
Transactional e-mail via Resend (https://resend.com) — simple HTTP API,
zero-cost on the free tier (3,000 emails/month), no SMTP/port hassles on
Vercel serverless.

Configure:
  RESEND_API_KEY   — API key from the Resend dashboard (required)
  RESEND_FROM      — verified sender, e.g. "geradorxml <no-reply@geradorxml.com.br>"
                     (defaults to Resend's shared sandbox sender, which only
                     works for testing — verify your own domain for production)
"""
import os
import requests

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM    = os.environ.get("RESEND_FROM", "geradorxml <onboarding@resend.dev>")
RESEND_URL     = "https://api.resend.com/emails"


def send_email(to: str, subject: str, html: str, text: str | None = None) -> bool:
    """
    Send a transactional email. Returns True on success, False on any failure
    (never raises — callers should treat email as best-effort and not leak
    whether an address exists based on send failures).
    """
    if not RESEND_API_KEY:
        return False
    try:
        resp = requests.post(
            RESEND_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": RESEND_FROM,
                "to": [to],
                "subject": subject,
                "html": html,
                **({"text": text} if text else {}),
            },
            timeout=15,
        )
        return resp.status_code < 300
    except Exception:
        return False


def send_password_reset_code(to: str, nome: str, code: str) -> bool:
    safe_nome = (nome or "").split(" ")[0] or "tudo bem"
    html = f"""\
<div style="font-family:Inter,Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#1e293b">
  <h2 style="color:#16a34a;margin-bottom:4px">geradorxml</h2>
  <p>Olá, {safe_nome}!</p>
  <p>Recebemos uma solicitação para redefinir a senha da sua conta. Use o código abaixo
     para continuar (válido por 15 minutos):</p>
  <div style="font-size:28px;font-weight:700;letter-spacing:6px;background:#f1f5f9;
              border-radius:10px;padding:16px 0;text-align:center;margin:20px 0">
    {code}
  </div>
  <p style="font-size:13px;color:#64748b">
    Se você não solicitou essa redefinição, ignore este e-mail — sua senha
    continuará a mesma e nenhuma ação será tomada.
  </p>
</div>
"""
    text = (f"Olá, {safe_nome}!\n\n"
            f"Use o código {code} para redefinir sua senha (válido por 15 minutos).\n\n"
            f"Se você não solicitou isso, ignore este e-mail.")
    return send_email(to, "Seu código de redefinição de senha — geradorxml", html, text)

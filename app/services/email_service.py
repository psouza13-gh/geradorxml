"""
Transactional e-mail. Two backends supported:

  • Resend (default) — RESEND_API_KEY + RESEND_FROM (verified domain sender).
  • Brevo (optional, more daily volume) — set BREVO_API_KEY + BREVO_SENDER
    and send_email() switches to Brevo automatically.

Env vars are read AT CALL TIME (not import) to avoid the Vercel warm-module
cache pitfall where a function imported before the env was available keeps
using stale/empty values.

Configure:
  RESEND_API_KEY / RESEND_FROM   — ex.: "GeradorXML <no-reply@hubfiscal.app.br>"
  BREVO_API_KEY / BREVO_SENDER / BREVO_SENDER_NAME   — opcional (motor Brevo)
"""
import os
import requests

RESEND_URL = "https://api.resend.com/emails"
BREVO_URL  = "https://api.brevo.com/v3/smtp/email"


def _send_resend(to: str, subject: str, html: str, text: str | None) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    sender  = os.environ.get("RESEND_FROM", "geradorxml <onboarding@resend.dev>").strip()
    if not api_key:
        return False
    try:
        resp = requests.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": sender,
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


def _send_brevo(to: str, subject: str, html: str, text: str | None) -> bool:
    api_key = os.environ.get("BREVO_API_KEY", "").strip()
    sender  = os.environ.get("BREVO_SENDER", "").strip()
    name    = os.environ.get("BREVO_SENDER_NAME", "GeradorXML").strip()
    if not api_key or not sender:
        return False
    try:
        resp = requests.post(
            BREVO_URL,
            headers={"api-key": api_key, "Content-Type": "application/json", "accept": "application/json"},
            json={
                "sender": {"name": name, "email": sender},
                "to": [{"email": to}],
                "subject": subject,
                "htmlContent": html,
                **({"textContent": text} if text else {}),
            },
            timeout=15,
        )
        return resp.status_code < 300
    except Exception:
        return False


def send_email(to: str, subject: str, html: str, text: str | None = None) -> bool:
    """
    Send a transactional email. Prefers Brevo when BREVO_API_KEY + BREVO_SENDER
    are set, otherwise Resend. Returns True on success, False on any failure
    (never raises — callers treat email as best-effort).
    """
    if os.environ.get("BREVO_API_KEY", "").strip() and os.environ.get("BREVO_SENDER", "").strip():
        return _send_brevo(to, subject, html, text)
    return _send_resend(to, subject, html, text)


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


# ── Reengajamento (leads que se cadastraram mas não baixaram) ─────────────────
# Estilo pessoal/transacional (sem imagens, 1 link) para evitar a aba Promoções.

APP_URL      = "https://geradorxml.hubfiscal.app.br/app"
WHATSAPP_URL = "https://wa.me/5551981355066"


def _first_name(nome: str | None) -> str:
    return ((nome or "").strip().split(" ")[0]) or "olá"


def _engagement_send(to: str, nome: str, subject: str, corpo_html: str, corpo_text: str) -> bool:
    nome1 = _first_name(nome)
    html = f"""\
<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:#1e293b;max-width:520px;margin:0 auto;padding:8px">
  <p>Oi, {nome1}!</p>
  {corpo_html}
  <p style="margin:24px 0">
    <a href="{APP_URL}" style="color:#16a34a;font-weight:700;text-decoration:underline">Acessar o GeradorXML pelo computador →</a>
  </p>
  <p style="color:#475569">Qualquer dúvida, é só responder este e-mail ou chamar no <a href="{WHATSAPP_URL}" style="color:#16a34a">WhatsApp</a>.</p>
  <p style="font-size:12px;color:#94a3b8;margin-top:28px">
    Você recebe este lembrete porque criou uma conta no GeradorXML. Se não quiser mais, responda este e-mail com “sair”.
  </p>
</div>"""
    text = (
        f"Oi, {nome1}!\n\n{corpo_text}\n\n"
        f"Acessar o GeradorXML pelo computador: {APP_URL}\n"
        f"Dúvidas no WhatsApp: {WHATSAPP_URL}\n\n"
        f"Você recebe este lembrete porque criou uma conta no GeradorXML. "
        f"Para não receber mais, responda com \"sair\"."
    )
    return send_email(to, subject, html, text)


def send_engagement_welcome(to: str, nome: str) -> bool:
    """Dia 1 — boas-vindas / como começar."""
    corpo_html = (
        "<p>Sua conta no <b>GeradorXML</b> já está pronta — você tem <b>5 CNPJs grátis, para sempre</b>.</p>"
        "<p>Para baixar as NFS-e, acesse pelo <b>computador do escritório</b>, onde normalmente está o "
        "<b>certificado digital A1 (.pfx)</b>. Em segundos você recebe um ZIP com todos os XMLs + uma planilha "
        "Excel organizada por emitidas e recebidas.</p>"
    )
    corpo_text = (
        "Sua conta no GeradorXML já está pronta — você tem 5 CNPJs grátis, para sempre.\n"
        "Para baixar as NFS-e, acesse pelo computador do escritório, onde está o certificado A1 (.pfx). "
        "Em segundos você recebe um ZIP com os XMLs + Excel organizado."
    )
    return _engagement_send(to, nome, "Sua conta no GeradorXML está pronta — falta só o 1º acesso", corpo_html, corpo_text)


def send_engagement_reminder(to: str, nome: str) -> bool:
    """Dia 3 — reforço com prova de valor."""
    corpo_html = (
        "<p>Vi que você criou a conta mas ainda não baixou nenhuma NFS-e.</p>"
        "<p>Um cliente nosso baixou <b>cerca de 5.000 XMLs em ~5 minutos</b> numa única consulta — o que antes "
        "levava horas de download manual, nota por nota, no portal.</p>"
        "<p>É só acessar pelo <b>computador</b> com o <b>certificado A1</b> e escolher o período. "
        "Seus 5 CNPJs grátis continuam te esperando.</p>"
    )
    corpo_text = (
        "Vi que você criou a conta mas ainda não baixou nenhuma NFS-e.\n"
        "Um cliente nosso baixou cerca de 5.000 XMLs em ~5 minutos numa única consulta.\n"
        "É só acessar pelo computador com o certificado A1 e escolher o período. Seus 5 CNPJs grátis continuam te esperando."
    )
    return _engagement_send(to, nome, "Baixe suas NFS-e em minutos — seus 5 CNPJs grátis te esperam", corpo_html, corpo_text)


def send_engagement_winback(to: str, nome: str) -> bool:
    """Dia 7 — última chamada + oferta de ajuda."""
    corpo_html = (
        "<p>Última chamada: sua conta com <b>5 CNPJs grátis</b> continua ativa, mas você ainda não testou.</p>"
        "<p>Se o que te travou foi o <b>certificado</b> (ele fica no computador do escritório) ou qualquer outra "
        "dúvida, me responde aqui ou chama no WhatsApp que eu te ajudo a fazer o primeiro download.</p>"
    )
    corpo_text = (
        "Última chamada: sua conta com 5 CNPJs grátis continua ativa, mas você ainda não testou.\n"
        "Se o que travou foi o certificado (fica no computador do escritório) ou outra dúvida, me responde ou "
        "chama no WhatsApp que eu ajudo no primeiro download."
    )
    return _engagement_send(to, nome, "Posso te ajudar a baixar sua primeira NFS-e?", corpo_html, corpo_text)

"""
/api/auth — authentication endpoints (Vercel serverless)

  POST /api/auth/register  — create account, returns JWT
  POST /api/auth/login     — verify credentials, returns JWT
  POST /api/auth/google    — sign in / sign up with a Google ID token, returns JWT
  GET  /api/auth/me        — return current user info (requires JWT)
"""
import sys, os, re, secrets
from flask import Flask, request, jsonify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.auth_service import (
    create_user, get_user_by_email, check_password,
    create_token, verify_token, get_user_by_id,
    cpf_already_registered, update_password,
    create_password_reset_code, verify_and_consume_reset_code,
)
from app.services.validators import validate_cpf, validate_telefone, format_cpf
from app.services.email_service import send_password_reset_code
from app.services.meta_capi_service import (
    track_lead as send_lead,
    track_trial_start as send_trial_start,
)
from app.services.subscription_service import get_uso_mensal
from app.services.rate_limit import limited

app = Flask(__name__)

# Limita o tamanho do corpo das requisições JSON (anti-DoS / poluição de banco).
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024  # 256 KB


def _client_ip() -> str:
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "unknown")

_CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
}


@app.after_request
def _cors(resp):
    for k, v in _CORS.items():
        resp.headers[k] = v
    return resp


def _user_json(user: dict, with_uso: bool = False) -> dict:
    d = {
        "id":                str(user["id"]),
        "nome":              user["nome"],
        "email":             user["email"],
        "plano":             user["plano"],
        "cnpj_limite":       user["cnpj_limite"],
        "status":            user["status"],
        "trial_expires_at":  user["trial_expires_at"].isoformat() if user.get("trial_expires_at") else None,
        "trial_locked_cnpj": user.get("trial_locked_cnpj"),
        "is_admin":          bool(user.get("is_admin")),
        "vitalicio":         bool(user.get("vitalicio")),
        "acesso_expires_at": user["acesso_expires_at"].isoformat() if user.get("acesso_expires_at") else None,
    }
    if with_uso:
        d["uso_mensal"] = get_uso_mensal(str(user["id"]))
    return d


def _get_auth_payload():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return verify_token(auth[7:])


# ── OPTIONS (pre-flight) ──────────────────────────────────────────────────────

@app.route("/api/auth/<path:_>", methods=["OPTIONS"])
@app.route("/api/auth",          methods=["OPTIONS"])
def preflight(_=None):
    return app.response_class("", 204)


# ── POST /api/auth/register ───────────────────────────────────────────────────

@app.route("/api/auth/register", methods=["POST"])
def register():
    if limited(f"register:{_client_ip()}", limit=6, window=600):
        return jsonify({"error": "Muitas contas criadas a partir deste acesso. Tente novamente mais tarde."}), 429

    data     = request.get_json(silent=True) or {}
    nome     = (data.get("nome")     or "").strip()
    email    = (data.get("email")    or "").strip().lower()
    senha    = data.get("senha")     or ""
    cpf      = (data.get("cpf")      or "").strip()
    telefone = (data.get("telefone") or "").strip()

    if not nome:
        return jsonify({"error": "Nome é obrigatório."}), 400
    # Tetos de tamanho (anti-poluição de banco / payload abusivo).
    if len(nome) > 120 or len(email) > 254 or len(senha) > 200 or len(cpf) > 20 or len(telefone) > 20:
        return jsonify({"error": "Dados muito longos."}), 400
    if not email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": "E-mail inválido."}), 400
    if len(senha) < 6:
        return jsonify({"error": "Senha deve ter ao menos 6 caracteres."}), 400
    # CPF and telefone are required at signup specifically to deter mass /
    # throwaway trial-account creation (one trial per real person).
    if not validate_cpf(cpf):
        return jsonify({"error": "CPF inválido. Verifique os números digitados."}), 400
    if not validate_telefone(telefone):
        return jsonify({"error": "Telefone inválido. Use o formato (DDD) 9XXXX-XXXX."}), 400

    try:
        if get_user_by_email(email):
            return jsonify({"error": "E-mail já cadastrado."}), 409
        if cpf_already_registered(cpf):
            return jsonify({"error": f"Este CPF ({format_cpf(cpf)}) já possui uma conta cadastrada. "
                                     f"Faça login ou recupere sua senha."}), 409

        user = create_user(nome, email, senha, cpf=cpf, telefone=telefone)
        if not user:
            return jsonify({"error": "Erro ao criar conta. Tente novamente."}), 500

        token = create_token(str(user["id"]), email)

        # Fire-and-forget: notify Meta Ads (Conversions API) of the new lead /
        # trial start. No-ops silently if the integration isn't configured —
        # never blocks or fails the registration response.
        try:
            user_id = str(user["id"])
            src_url = request.headers.get("Referer") or f"https://{request.host}/register"
            ip      = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                       or request.remote_addr)
            ua      = request.headers.get("User-Agent")
            send_lead(user_id=user_id, email=email, telefone=telefone,
                      event_source_url=src_url, client_ip=ip, client_user_agent=ua)
            send_trial_start(user_id=user_id, email=email, telefone=telefone,
                             event_source_url=src_url, client_ip=ip, client_user_agent=ua)
        except Exception:
            pass

        return jsonify({"token": token, "user": _user_json(user)}), 201

    except Exception:
        return jsonify({"error": "Erro interno. Tente novamente."}), 500


# ── POST /api/auth/login ──────────────────────────────────────────────────────

@app.route("/api/auth/login", methods=["POST"])
def login():
    if limited(f"login:{_client_ip()}", limit=12, window=300):
        return jsonify({"error": "Muitas tentativas de login. Aguarde alguns minutos e tente novamente."}), 429

    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    senha = data.get("senha") or ""

    if not email or not senha:
        return jsonify({"error": "E-mail e senha são obrigatórios."}), 400

    try:
        user = get_user_by_email(email)
        if not user or not check_password(senha, user["password_hash"]):
            return jsonify({"error": "E-mail ou senha incorretos."}), 401

        token = create_token(str(user["id"]), email)
        return jsonify({"token": token, "user": _user_json(user, with_uso=True)})

    except Exception:
        return jsonify({"error": "Erro interno. Tente novamente."}), 500


# ── POST /api/auth/google ─────────────────────────────────────────────────────
# Login/cadastro com conta Google (Google Identity Services).
#
# O navegador envia a "credential" (um JWT assinado pelo Google) obtida pelo
# botão oficial "Entrar com Google". Aqui a assinatura é verificada com a
# biblioteca oficial google-auth (issuer, audience = nosso Client ID, expiração)
# — nunca confiamos no e-mail sem validar o token.
#
# Fluxo aditivo: NÃO altera o login/registro por senha.
#   • e-mail já cadastrado  → login normal (Google comprova posse do e-mail)
#   • e-mail novo           → cria conta trial com senha aleatória inutilizável;
#                             a pessoa pode definir senha real depois pelo
#                             "Esqueci minha senha" (mesmo fluxo já existente).
#
# GOOGLE_CLIENT_ID não é segredo (aparece no HTML de qualquer site com o botão);
# sem a env var configurada o endpoint responde 503 e o botão nem é exibido.

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()


@app.route("/api/auth/google", methods=["POST"])
def google_auth():
    if not GOOGLE_CLIENT_ID:
        return jsonify({"error": "Login com Google não está habilitado."}), 503
    if limited(f"google:{_client_ip()}", limit=12, window=300):
        return jsonify({"error": "Muitas tentativas. Aguarde alguns minutos e tente novamente."}), 429

    data       = request.get_json(silent=True) or {}
    credential = data.get("credential") or ""
    if not credential or not isinstance(credential, str) or len(credential) > 4096:
        return jsonify({"error": "Credencial do Google ausente ou inválida."}), 400

    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests
        info = google_id_token.verify_oauth2_token(
            credential, google_requests.Request(), GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=10,
        )
    except ImportError:
        return jsonify({"error": "Login com Google indisponível no momento."}), 503
    except Exception:
        return jsonify({"error": "Não foi possível validar sua conta Google. Tente novamente."}), 401

    email = (info.get("email") or "").strip().lower()
    if not email or not info.get("email_verified"):
        return jsonify({"error": "Sua conta Google não possui e-mail verificado."}), 401
    nome = (info.get("name") or email.split("@")[0]).strip()[:120]

    try:
        user   = get_user_by_email(email)
        is_new = user is None
        if is_new:
            # Senha aleatória de 43 chars: impossível de adivinhar, então o login
            # por senha simplesmente não funciona até a pessoa definir uma via
            # "Esqueci minha senha". Nenhuma mudança de schema necessária.
            user = create_user(nome, email, secrets.token_urlsafe(32))
            if not user:
                return jsonify({"error": "Erro ao criar conta. Tente novamente."}), 500

        token = create_token(str(user["id"]), email)

        if is_new:
            # Mesmo fire-and-forget do /register (Meta CAPI) — sem telefone,
            # pois o Google não fornece; nunca bloqueia a resposta.
            try:
                user_id = str(user["id"])
                src_url = request.headers.get("Referer") or f"https://{request.host}/register"
                ip      = _client_ip()
                ua      = request.headers.get("User-Agent")
                send_lead(user_id=user_id, email=email,
                          event_source_url=src_url, client_ip=ip, client_user_agent=ua)
                send_trial_start(user_id=user_id, email=email,
                                 event_source_url=src_url, client_ip=ip, client_user_agent=ua)
            except Exception:
                pass

        return jsonify({"token": token, "user": _user_json(user, with_uso=not is_new),
                        "is_new": is_new}), (201 if is_new else 200)

    except Exception:
        return jsonify({"error": "Erro interno. Tente novamente."}), 500


# ── POST /api/auth/forgot-password ────────────────────────────────────────────
# Request a 6-digit reset code by email. Always returns a generic
# "se o e-mail existir, enviamos um código" response — never reveals
# whether the address has an account (prevents account enumeration).

@app.route("/api/auth/forgot-password", methods=["POST"])
def forgot_password():
    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    generic = jsonify({"message": "Se este e-mail estiver cadastrado, enviaremos um código de redefinição em instantes."})

    if limited(f"forgot:{_client_ip()}", limit=6, window=900):
        return jsonify({"error": "Muitas solicitações. Aguarde alguns minutos e tente novamente."}), 429

    if not email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return generic

    try:
        user = get_user_by_email(email)
        if user:
            code = create_password_reset_code(str(user["id"]))
            if code:  # None means "cooldown" — silently skip re-send
                send_password_reset_code(user["email"], user["nome"], code)
        return generic
    except Exception:
        return generic


# ── POST /api/auth/reset-password ─────────────────────────────────────────────
# Confirm the emailed code + set a new password.

@app.route("/api/auth/reset-password", methods=["POST"])
def reset_password():
    if limited(f"reset:{_client_ip()}", limit=10, window=600):
        return jsonify({"error": "Muitas tentativas. Aguarde alguns minutos e tente novamente."}), 429

    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    code  = (data.get("code")  or "").strip()
    senha = data.get("senha")  or ""

    if not email or not code:
        return jsonify({"error": "Informe o e-mail e o código recebido."}), 400
    if len(senha) < 6:
        return jsonify({"error": "A nova senha deve ter ao menos 6 caracteres."}), 400

    try:
        user = get_user_by_email(email)
        if not user or not verify_and_consume_reset_code(str(user["id"]), code):
            return jsonify({"error": "Código inválido ou expirado. Solicite um novo."}), 400

        update_password(str(user["id"]), senha)
        return jsonify({"message": "Senha redefinida com sucesso. Você já pode entrar com a nova senha."})

    except Exception:
        return jsonify({"error": "Erro interno. Tente novamente."}), 500


# ── GET /api/auth/me ──────────────────────────────────────────────────────────

@app.route("/api/auth/me", methods=["GET"])
def me():
    payload = _get_auth_payload()
    if not payload:
        return jsonify({"error": "Não autenticado."}), 401

    try:
        user = get_user_by_id(payload["sub"])
        if not user:
            return jsonify({"error": "Usuário não encontrado."}), 404

        return jsonify(_user_json(user, with_uso=True))

    except Exception:
        return jsonify({"error": "Erro interno."}), 500

"""
/api/auth — authentication endpoints (Vercel serverless)

  POST /api/auth/register  — create account, returns JWT
  POST /api/auth/login     — verify credentials, returns JWT
  GET  /api/auth/me        — return current user info (requires JWT)
"""
import sys, os, re
from flask import Flask, request, jsonify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.auth_service import (
    create_user, get_user_by_email, check_password,
    create_token, verify_token, get_user_by_id,
)
from app.services.subscription_service import get_uso_mensal

app = Flask(__name__)

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
    data  = request.get_json(silent=True) or {}
    nome  = (data.get("nome")  or "").strip()
    email = (data.get("email") or "").strip().lower()
    senha = data.get("senha")  or ""

    if not nome:
        return jsonify({"error": "Nome é obrigatório."}), 400
    if not email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": "E-mail inválido."}), 400
    if len(senha) < 6:
        return jsonify({"error": "Senha deve ter ao menos 6 caracteres."}), 400

    try:
        if get_user_by_email(email):
            return jsonify({"error": "E-mail já cadastrado."}), 409

        user = create_user(nome, email, senha)
        if not user:
            return jsonify({"error": "Erro ao criar conta. Tente novamente."}), 500

        token = create_token(str(user["id"]), email)
        return jsonify({"token": token, "user": _user_json(user)}), 201

    except Exception:
        return jsonify({"error": "Erro interno. Tente novamente."}), 500


# ── POST /api/auth/login ──────────────────────────────────────────────────────

@app.route("/api/auth/login", methods=["POST"])
def login():
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

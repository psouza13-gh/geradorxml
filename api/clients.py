"""
/api/clients — saved clients CRUD (Vercel serverless)

  GET    /api/clients          — list user's clients
  POST   /api/clients          — create client
  PUT    /api/clients/<id>     — update client (blocked if trial-locked)
  DELETE /api/clients/<id>     — delete client (blocked if trial-locked)
"""
import sys, os, uuid, re
from datetime import datetime, timezone
from flask import Flask, request, jsonify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.auth_service import verify_token
from app.services.subscription_service import (
    cliente_bloqueado_por_trial,
    get_cnpj_limite,
)
from app.services.db import execute

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


def _require_auth():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return verify_token(auth[7:])


def _strip_cnpj(v: str) -> str:
    return re.sub(r"\D", "", v or "")


# ── OPTIONS ───────────────────────────────────────────────────────────────────

@app.route("/api/clients",         methods=["OPTIONS"])
@app.route("/api/clients/<path:_>", methods=["OPTIONS"])
def preflight(_=None):
    return app.response_class("", 204)


# ── Collection ────────────────────────────────────────────────────────────────

@app.route("/api/clients", methods=["GET", "POST"])
def clients():
    payload = _require_auth()
    if not payload:
        return jsonify({"error": "Não autenticado."}), 401

    user_id = payload["sub"]

    # ── GET — list ─────────────────────────────────────────────────────────
    if request.method == "GET":
        try:
            rows = execute(
                "SELECT * FROM clients WHERE user_id = %s ORDER BY nome ASC",
                (user_id,),
                fetch="all",
            )
            # Fetch the user's trial_locked_cnpj to tag locked clients
            user = execute(
                "SELECT plano, trial_locked_cnpj FROM users WHERE id = %s",
                (user_id,),
                fetch="one",
            )
            locked_cnpj = (user or {}).get("trial_locked_cnpj")
            is_trial    = ((user or {}).get("plano") or "").lower() == "trial"

            result = []
            for r in (rows or []):
                result.append({
                    "id":               str(r["id"]),
                    "nome":             r["nome"],
                    "cnpj":             r["cnpj"],
                    "municipio_codigo": r["municipio_codigo"] or "",
                    "municipio_nome":   r["municipio_nome"]   or "",
                    "created_at":       r["created_at"].isoformat() if r["created_at"] else None,
                    "locked":           bool(is_trial and locked_cnpj and r["cnpj"] == locked_cnpj),
                })
            return jsonify({"clients": result})
        except Exception:
            return jsonify({"error": "Erro ao buscar clientes."}), 500

    # ── POST — create ──────────────────────────────────────────────────────
    data             = request.get_json(silent=True) or {}
    nome             = (data.get("nome")             or "").strip()
    cnpj             = _strip_cnpj(data.get("cnpj")  or "")
    municipio_codigo = (data.get("municipio_codigo") or "").strip()
    municipio_nome   = (data.get("municipio_nome")   or "").strip()

    if not nome:
        return jsonify({"error": "Nome é obrigatório."}), 400
    if not cnpj or len(cnpj) != 14:
        return jsonify({"error": "CNPJ inválido (14 dígitos sem máscara)."}), 400

    try:
        user = execute(
            "SELECT plano, cnpj_limite FROM users WHERE id = %s",
            (user_id,),
            fetch="one",
        )
        if not user:
            return jsonify({"error": "Usuário não encontrado."}), 404

        # Plan-level client cap
        limite = get_cnpj_limite(user["plano"])
        if limite != -1:
            count = execute(
                "SELECT COUNT(*) AS c FROM clients WHERE user_id = %s",
                (user_id,),
                fetch="one",
            )
            if count and count["c"] >= limite:
                return jsonify({
                    "error": f'Limite de {limite} cliente(s) atingido no plano '
                             f'{user["plano"].capitalize()}. Faça upgrade para adicionar mais.',
                }), 403

        # Duplicate CNPJ guard
        existing = execute(
            "SELECT id FROM clients WHERE user_id = %s AND cnpj = %s",
            (user_id, cnpj),
            fetch="one",
        )
        if existing:
            return jsonify({"error": "CNPJ já cadastrado."}), 409

        client_id = str(uuid.uuid4())
        now       = datetime.now(timezone.utc)

        execute(
            """
            INSERT INTO clients
                (id, user_id, nome, cnpj, municipio_codigo, municipio_nome, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (client_id, user_id, nome, cnpj, municipio_codigo, municipio_nome, now),
        )

        return jsonify({
            "id":               client_id,
            "nome":             nome,
            "cnpj":             cnpj,
            "municipio_codigo": municipio_codigo,
            "municipio_nome":   municipio_nome,
            "locked":           False,
        }), 201

    except Exception:
        return jsonify({"error": "Erro ao criar cliente."}), 500


# ── Single resource ───────────────────────────────────────────────────────────

@app.route("/api/clients/<client_id>", methods=["PUT", "DELETE"])
def client_detail(client_id: str):
    payload = _require_auth()
    if not payload:
        return jsonify({"error": "Não autenticado."}), 401

    user_id = payload["sub"]

    # ── PUT — update ───────────────────────────────────────────────────────
    if request.method == "PUT":
        if cliente_bloqueado_por_trial(user_id, client_id):
            return jsonify({
                "error": "Este cliente está bloqueado: o CNPJ do trial não pode ser "
                         "alterado após o primeiro download.",
            }), 403

        data             = request.get_json(silent=True) or {}
        nome             = (data.get("nome")             or "").strip()
        cnpj             = _strip_cnpj(data.get("cnpj")  or "")
        municipio_codigo = (data.get("municipio_codigo") or "").strip()
        municipio_nome   = (data.get("municipio_nome")   or "").strip()

        if not nome:
            return jsonify({"error": "Nome é obrigatório."}), 400
        if not cnpj or len(cnpj) != 14:
            return jsonify({"error": "CNPJ inválido."}), 400

        try:
            execute(
                """
                UPDATE clients
                SET nome = %s, cnpj = %s, municipio_codigo = %s, municipio_nome = %s
                WHERE id = %s AND user_id = %s
                """,
                (nome, cnpj, municipio_codigo, municipio_nome, client_id, user_id),
            )
            return jsonify({
                "id":               client_id,
                "nome":             nome,
                "cnpj":             cnpj,
                "municipio_codigo": municipio_codigo,
                "municipio_nome":   municipio_nome,
            })
        except Exception:
            return jsonify({"error": "Erro ao atualizar cliente."}), 500

    # ── DELETE ─────────────────────────────────────────────────────────────
    if cliente_bloqueado_por_trial(user_id, client_id):
        return jsonify({
            "error": "Este cliente está bloqueado: não é possível excluir o CNPJ "
                     "do trial após o primeiro download.",
        }), 403

    try:
        execute(
            "DELETE FROM clients WHERE id = %s AND user_id = %s",
            (client_id, user_id),
        )
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"error": "Erro ao excluir cliente."}), 500

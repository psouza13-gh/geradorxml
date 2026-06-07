"""
/api/admin — Super Admin panel endpoints (Vercel serverless)

  GET    /api/admin/stats                 — dashboard summary (counts, MRR, usage)
  GET    /api/admin/users?q=&status=&plano=&page= — list/search users
  POST   /api/admin/users/<id>/grant      — grant/edit a manual subscription
                                            (temporária ou vitalícia, com/sem limite de CNPJs)
  POST   /api/admin/users/<id>/freeze     — freeze account (blocks usage, keeps data)
  POST   /api/admin/users/<id>/unfreeze   — restore a frozen account
  DELETE /api/admin/users/<id>/subscription — cancel/remove the user's subscription

All routes require a JWT for a user with is_admin = TRUE.
"""
import sys, os, re
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.auth_service import verify_token
from app.services.db import execute

app = Flask(__name__)

_CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
}


@app.after_request
def _cors(resp):
    for k, v in _CORS.items():
        resp.headers[k] = v
    return resp


# Plan catalog — keep in sync with api/subscribe.py and subscription_service.PLANO_LIMITES
_PLANOS = {
    "trial":   {"valor": 0.0,   "limite": 1},
    "starter": {"valor": 97.0,  "limite": 10},
    "pro":     {"valor": 297.0, "limite": 50},
    "office":  {"valor": 597.0, "limite": 150},
    "bpo":     {"valor": 997.0, "limite": -1},
}

_STATUSES = {"ativo", "suspenso", "cancelado", "congelado"}
_TIPOS_GRANT = {"temporaria", "vitalicia"}


# ── Auth guard ────────────────────────────────────────────────────────────────

def _require_admin():
    """Return the admin's JWT payload, or None if not authenticated/admin."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    payload = verify_token(auth[7:])
    if not payload:
        return None
    row = execute("SELECT is_admin FROM users WHERE id = %s", (payload["sub"],), fetch="one")
    if not row or not row["is_admin"]:
        return None
    return payload


# ── OPTIONS (pre-flight) ──────────────────────────────────────────────────────

@app.route("/api/admin/<path:_>", methods=["OPTIONS"])
@app.route("/api/admin",          methods=["OPTIONS"])
def preflight(_=None):
    return app.response_class("", 204)


# ── GET /api/admin/stats ──────────────────────────────────────────────────────

@app.route("/api/admin/stats", methods=["GET"])
def stats():
    if not _require_admin():
        return jsonify({"error": "Acesso restrito ao administrador."}), 403

    try:
        total = execute("SELECT COUNT(*) AS n FROM users", fetch="one")["n"]

        por_status_rows = execute(
            "SELECT status, COUNT(*) AS n FROM users GROUP BY status", fetch="all"
        )
        por_status = {r["status"]: r["n"] for r in (por_status_rows or [])}

        por_plano_rows = execute(
            "SELECT plano, COUNT(*) AS n FROM users GROUP BY plano", fetch="all"
        )
        por_plano = {r["plano"]: r["n"] for r in (por_plano_rows or [])}

        por_origem_rows = execute(
            "SELECT plano_origem, COUNT(*) AS n FROM users GROUP BY plano_origem", fetch="all"
        )
        por_origem = {r["plano_origem"]: r["n"] for r in (por_origem_rows or [])}

        # Paying subscribers = active, paid plan, billed via ASAAS
        pagantes_row = execute(
            """
            SELECT COUNT(*) AS n FROM users
             WHERE status = 'ativo' AND plano <> 'trial' AND plano_origem = 'asaas'
            """,
            fetch="one",
        )
        pagantes = pagantes_row["n"]

        # MRR — sum of plan prices for active ASAAS-billed subscribers
        mrr_rows = execute(
            """
            SELECT plano, COUNT(*) AS n FROM users
             WHERE status = 'ativo' AND plano <> 'trial' AND plano_origem = 'asaas'
             GROUP BY plano
            """,
            fetch="all",
        )
        mrr = sum(_PLANOS.get(r["plano"], {}).get("valor", 0.0) * r["n"] for r in (mrr_rows or []))

        # Manual grants (admin-issued access)
        vitalicios_row = execute(
            "SELECT COUNT(*) AS n FROM users WHERE plano_origem = 'admin_vitalicio'", fetch="one"
        )
        temporarios_row = execute(
            "SELECT COUNT(*) AS n FROM users WHERE plano_origem = 'admin_temporario' AND status <> 'cancelado'",
            fetch="one",
        )

        # New signups (last 30 days)
        novos_row = execute(
            "SELECT COUNT(*) AS n FROM users WHERE created_at >= NOW() - INTERVAL '30 days'",
            fetch="one",
        )

        # Usage this month
        mes = datetime.now(timezone.utc).strftime("%Y-%m")
        uso_row = execute(
            """
            SELECT COALESCE(SUM(download_count), 0) AS downloads,
                   COUNT(DISTINCT cnpj)              AS cnpjs,
                   COUNT(DISTINCT user_id)           AS usuarios_ativos
              FROM monthly_usage WHERE mes = %s
            """,
            (mes,),
            fetch="one",
        )

        return jsonify({
            "total_usuarios":       total,
            "por_status":           por_status,
            "por_plano":            por_plano,
            "por_origem":           por_origem,
            "assinantes_pagantes":  pagantes,
            "mrr_estimado":         round(mrr, 2),
            "acessos_vitalicios":   vitalicios_row["n"],
            "acessos_temporarios":  temporarios_row["n"],
            "novos_30d":            novos_row["n"],
            "uso_mes": {
                "mes":             mes,
                "downloads":       int(uso_row["downloads"]),
                "cnpjs_unicos":    uso_row["cnpjs"],
                "usuarios_ativos": uso_row["usuarios_ativos"],
            },
            "planos_catalogo": _PLANOS,
        })
    except Exception as exc:
        return jsonify({"error": f"Erro ao carregar estatísticas: {exc}"}), 500


# ── GET /api/admin/users ──────────────────────────────────────────────────────

@app.route("/api/admin/users", methods=["GET"])
def list_users():
    if not _require_admin():
        return jsonify({"error": "Acesso restrito ao administrador."}), 403

    q       = (request.args.get("q") or "").strip().lower()
    status  = (request.args.get("status") or "").strip().lower()
    plano   = (request.args.get("plano") or "").strip().lower()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    page_size = 50
    offset = (page - 1) * page_size

    where  = []
    params = []
    if q:
        where.append("(LOWER(nome) LIKE %s OR LOWER(email) LIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    if status in _STATUSES:
        where.append("status = %s")
        params.append(status)
    if plano in _PLANOS:
        where.append("plano = %s")
        params.append(plano)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    try:
        total = execute(
            f"SELECT COUNT(*) AS n FROM users {where_sql}", tuple(params), fetch="one"
        )["n"]

        rows = execute(
            f"""
            SELECT id, nome, email, plano, cnpj_limite, status,
                   trial_expires_at, vitalicio, acesso_expires_at,
                   plano_origem, is_admin, asaas_customer_id, created_at
              FROM users
              {where_sql}
             ORDER BY created_at DESC
             LIMIT %s OFFSET %s
            """,
            tuple(params + [page_size, offset]),
            fetch="all",
        )

        users = []
        for r in (rows or []):
            users.append({
                "id":                str(r["id"]),
                "nome":              r["nome"],
                "email":             r["email"],
                "plano":             r["plano"],
                "cnpj_limite":       r["cnpj_limite"],
                "status":            r["status"],
                "trial_expires_at":  r["trial_expires_at"].isoformat() if r["trial_expires_at"] else None,
                "vitalicio":         bool(r["vitalicio"]),
                "acesso_expires_at": r["acesso_expires_at"].isoformat() if r["acesso_expires_at"] else None,
                "plano_origem":      r["plano_origem"],
                "is_admin":          bool(r["is_admin"]),
                "tem_asaas":         bool(r["asaas_customer_id"]),
                "created_at":        r["created_at"].isoformat() if r["created_at"] else None,
            })

        return jsonify({
            "users": users,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
        })
    except Exception as exc:
        return jsonify({"error": f"Erro ao buscar usuários: {exc}"}), 500


# ── POST /api/admin/users/<id>/grant ──────────────────────────────────────────
# Concede (ou edita) uma assinatura manual: temporária ou vitalícia,
# com limite de CNPJs do plano, personalizado, ou ilimitado.

@app.route("/api/admin/users/<user_id>/grant", methods=["POST"])
def grant_subscription(user_id):
    if not _require_admin():
        return jsonify({"error": "Acesso restrito ao administrador."}), 403

    data  = request.get_json(silent=True) or {}
    plano = (data.get("plano") or "").strip().lower()
    tipo  = (data.get("tipo")  or "").strip().lower()
    limite_modo = (data.get("limite_modo") or "plano").strip().lower()  # plano | personalizado | ilimitado
    expires_raw = (data.get("expires_at") or "").strip()

    if plano not in _PLANOS:
        return jsonify({"error": "Plano inválido."}), 400
    if tipo not in _TIPOS_GRANT:
        return jsonify({"error": "Tipo inválido. Use 'temporaria' ou 'vitalicia'."}), 400

    # Resolve cnpj_limite
    if limite_modo == "ilimitado":
        cnpj_limite = -1
    elif limite_modo == "personalizado":
        try:
            cnpj_limite = int(data.get("cnpj_limite"))
            if cnpj_limite < 1:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "Informe um limite de CNPJs personalizado válido (>= 1)."}), 400
    else:
        cnpj_limite = _PLANOS[plano]["limite"]

    expires_at = None
    if tipo == "temporaria":
        if not expires_raw:
            return jsonify({"error": "Informe a data de expiração do acesso temporário."}), 400
        try:
            # Accept "YYYY-MM-DD" or full ISO datetime
            if re.match(r"^\d{4}-\d{2}-\d{2}$", expires_raw):
                expires_at = datetime.strptime(expires_raw, "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59, tzinfo=timezone.utc
                )
            else:
                expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
        except ValueError:
            return jsonify({"error": "Data de expiração inválida. Use o formato AAAA-MM-DD."}), 400

        if expires_at <= datetime.now(timezone.utc):
            return jsonify({"error": "A data de expiração deve estar no futuro."}), 400

    vitalicio    = (tipo == "vitalicia")
    plano_origem = "admin_vitalicio" if vitalicio else "admin_temporario"

    try:
        user = execute("SELECT id FROM users WHERE id = %s", (user_id,), fetch="one")
        if not user:
            return jsonify({"error": "Usuário não encontrado."}), 404

        execute(
            """
            UPDATE users
               SET plano             = %s,
                   cnpj_limite       = %s,
                   status            = 'ativo',
                   status_anterior   = NULL,
                   trial_expires_at  = NULL,
                   trial_locked_cnpj = NULL,
                   vitalicio         = %s,
                   acesso_expires_at = %s,
                   plano_origem      = %s
             WHERE id = %s
            """,
            (plano, cnpj_limite, vitalicio, expires_at, plano_origem, user_id),
        )

        return jsonify({
            "ok": True,
            "msg": "Acesso concedido com sucesso.",
            "plano": plano,
            "cnpj_limite": cnpj_limite,
            "tipo": tipo,
            "expires_at": expires_at.isoformat() if expires_at else None,
        })
    except Exception as exc:
        return jsonify({"error": f"Erro ao conceder acesso: {exc}"}), 500


# ── POST /api/admin/users/<id>/freeze ─────────────────────────────────────────

@app.route("/api/admin/users/<user_id>/freeze", methods=["POST"])
def freeze_user(user_id):
    if not _require_admin():
        return jsonify({"error": "Acesso restrito ao administrador."}), 403

    try:
        user = execute("SELECT id, status, is_admin FROM users WHERE id = %s", (user_id,), fetch="one")
        if not user:
            return jsonify({"error": "Usuário não encontrado."}), 404
        if user["is_admin"]:
            return jsonify({"error": "Não é possível congelar uma conta de administrador."}), 400
        if user["status"] == "congelado":
            return jsonify({"ok": True, "msg": "Conta já está congelada."})

        execute(
            "UPDATE users SET status_anterior = status, status = 'congelado' WHERE id = %s",
            (user_id,),
        )
        return jsonify({"ok": True, "msg": "Conta congelada. O usuário não conseguirá usar a ferramenta até ser reativada."})
    except Exception as exc:
        return jsonify({"error": f"Erro ao congelar conta: {exc}"}), 500


# ── POST /api/admin/users/<id>/unfreeze ───────────────────────────────────────

@app.route("/api/admin/users/<user_id>/unfreeze", methods=["POST"])
def unfreeze_user(user_id):
    if not _require_admin():
        return jsonify({"error": "Acesso restrito ao administrador."}), 403

    try:
        user = execute("SELECT id, status, status_anterior FROM users WHERE id = %s", (user_id,), fetch="one")
        if not user:
            return jsonify({"error": "Usuário não encontrado."}), 404
        if user["status"] != "congelado":
            return jsonify({"ok": True, "msg": "Conta não está congelada."})

        restore_to = user["status_anterior"] or "ativo"
        execute(
            "UPDATE users SET status = %s, status_anterior = NULL WHERE id = %s",
            (restore_to, user_id),
        )
        return jsonify({"ok": True, "msg": f"Conta reativada (status: {restore_to})."})
    except Exception as exc:
        return jsonify({"error": f"Erro ao reativar conta: {exc}"}), 500


# ── DELETE /api/admin/users/<id>/subscription ─────────────────────────────────
# Remove/cancela a assinatura do usuário, devolvendo-o ao estado "sem plano".

@app.route("/api/admin/users/<user_id>/subscription", methods=["DELETE"])
def delete_subscription(user_id):
    if not _require_admin():
        return jsonify({"error": "Acesso restrito ao administrador."}), 403

    try:
        user = execute("SELECT id, is_admin FROM users WHERE id = %s", (user_id,), fetch="one")
        if not user:
            return jsonify({"error": "Usuário não encontrado."}), 404
        if user["is_admin"]:
            return jsonify({"error": "Não é possível excluir a assinatura de uma conta de administrador."}), 400

        now = datetime.now(timezone.utc)
        execute(
            """
            UPDATE users
               SET plano             = 'trial',
                   cnpj_limite       = 1,
                   status            = 'cancelado',
                   status_anterior   = NULL,
                   vitalicio         = FALSE,
                   acesso_expires_at = NULL,
                   plano_origem      = 'trial',
                   trial_expires_at  = %s
             WHERE id = %s
            """,
            (now - timedelta(seconds=1), user_id),
        )
        return jsonify({"ok": True, "msg": "Assinatura removida. Conta voltou ao estado padrão (cancelada/sem plano ativo)."})
    except Exception as exc:
        return jsonify({"error": f"Erro ao excluir assinatura: {exc}"}), 500

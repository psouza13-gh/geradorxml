"""
/api/admin — Super Admin panel endpoints (Vercel serverless)

  GET    /api/admin/stats                 — dashboard summary (counts, MRR, usage)
  GET    /api/admin/users?q=&status=&plano=&page= — list/search users
  POST   /api/admin/users/<id>/grant      — grant/edit a manual subscription
                                            (temporária ou vitalícia, com/sem limite de CNPJs)
  POST   /api/admin/users/<id>/freeze     — freeze account (blocks usage, keeps data)
  POST   /api/admin/users/<id>/unfreeze   — restore a frozen account
  DELETE /api/admin/users/<id>/subscription — cancel/remove the user's subscription

  GET    /api/admin/integrations/meta       — Meta Conversions API config + status
  POST   /api/admin/integrations/meta       — update pixel id / event toggles / test code
  POST   /api/admin/integrations/meta/test  — send a test event to Meta

All routes require a JWT for a user with is_admin = TRUE.
"""
import sys, os, re, io, csv
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, Response

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.auth_service import verify_token
from app.services.db import execute
from app.services.crypto_service import decrypt
from app.services import meta_capi_service as meta_capi
from app.services.email_service import (
    send_engagement_welcome,
    send_engagement_reminder,
    send_engagement_winback,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024  # 256 KB — anti-DoS / payload abusivo

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
    "trial":   {"valor": 0.0,   "limite": 5},
    "starter": {"valor": 67.0,  "limite": 15},
    "pro":     {"valor": 147.0, "limite": 50},
    "office":  {"valor": 297.0, "limite": 150},
    "bpo":     {"valor": 597.0, "limite": -1},
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

        # Paying subscribers = active, paid plan, billed via ASAAS or Stripe
        pagantes_row = execute(
            """
            SELECT COUNT(*) AS n FROM users
             WHERE status = 'ativo' AND plano <> 'trial' AND plano_origem IN ('asaas', 'stripe')
            """,
            fetch="one",
        )
        pagantes = pagantes_row["n"]

        # MRR — sum of plan prices for active ASAAS or Stripe-billed subscribers
        mrr_rows = execute(
            """
            SELECT plano, COUNT(*) AS n FROM users
             WHERE status = 'ativo' AND plano <> 'trial' AND plano_origem IN ('asaas', 'stripe')
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

        # 1. Trial-to-paid conversion rate (excluding admins)
        converted_row = execute(
            "SELECT COUNT(*) AS n FROM users WHERE plano_origem IN ('asaas', 'stripe') AND is_admin = FALSE",
            fetch="one"
        )
        converted_count = converted_row["n"] if converted_row else 0

        # Free plan has no time expiry — "eligible" = paid + free users who
        # actually activated (made at least one successful download).
        eligible_row = execute(
            """
            SELECT COUNT(*) AS n FROM users u
             WHERE u.is_admin = FALSE
               AND (u.plano_origem IN ('asaas', 'stripe')
                    OR (u.plano = 'trial' AND EXISTS (
                          SELECT 1 FROM download_logs d
                           WHERE d.user_id = u.id AND d.sucesso = TRUE
                    )))
            """,
            fetch="one"
        )
        eligible_count = eligible_row["n"] if eligible_row else 0
        trial_conversion_rate = (converted_count / eligible_count * 100) if eligible_count > 0 else 0.0

        # 2. Churn Rate (last 30 days)
        cancelados_30d_row = execute(
            """
            SELECT COUNT(*) AS n FROM users
             WHERE status = 'cancelado'
               AND plano_origem IN ('asaas', 'stripe')
               AND cancelled_at >= NOW() - INTERVAL '30 days'
            """,
            fetch="one"
        )
        cancelados_30d = cancelados_30d_row["n"] if cancelados_30d_row else 0

        ativos_row = execute(
            """
            SELECT COUNT(*) AS n FROM users
             WHERE status IN ('ativo', 'suspenso')
               AND plano_origem IN ('asaas', 'stripe')
            """,
            fetch="one"
        )
        ativos_count = ativos_row["n"] if ativos_row else 0

        total_churn_base = ativos_count + cancelados_30d
        churn_rate = (cancelados_30d / total_churn_base * 100) if total_churn_base > 0 else 0.0

        # 3. Estimated LTV
        arpu = (mrr / ativos_count) if ativos_count > 0 else 0.0
        if churn_rate > 0:
            ltv = arpu / (churn_rate / 100.0)
        else:
            # Fallback to lifetime churn to avoid infinity
            total_cancelados_row = execute(
                "SELECT COUNT(*) AS n FROM users WHERE status = 'cancelado' AND plano_origem IN ('asaas', 'stripe')",
                fetch="one"
            )
            total_cancelados = total_cancelados_row["n"] if total_cancelados_row else 0
            lifetime_churn_base = ativos_count + total_cancelados
            lifetime_churn = (total_cancelados / lifetime_churn_base * 100) if lifetime_churn_base > 0 else 0.0
            
            if lifetime_churn > 0:
                ltv = arpu / (lifetime_churn / 100.0)
            else:
                ltv = arpu * 24.0

        # 4. Revenue Distribution per Plan
        receita_dist = {}
        for p_id, p_info in _PLANOS.items():
            p_count_row = execute(
                """
                SELECT COUNT(*) AS n FROM users
                 WHERE status IN ('ativo', 'suspenso')
                   AND plano_origem IN ('asaas', 'stripe')
                   AND plano = %s
                """,
                (p_id,),
                fetch="one"
            )
            p_count = p_count_row["n"] if p_count_row else 0
            p_val = p_info.get("valor", 0.0)
            p_mrr = p_count * p_val
            receita_dist[p_id] = {
                "mrr": round(p_mrr, 2),
                "count": p_count,
                "pct": round((p_mrr / mrr * 100) if mrr > 0 else 0.0, 1)
            }

        # 5. Download Success/Failure Rate (Error Rate) - Last 30 days
        try:
            sucessos_row = execute(
                "SELECT COUNT(*) AS n FROM download_logs WHERE sucesso = TRUE AND created_at >= NOW() - INTERVAL '30 days'",
                fetch="one"
            )
            falhas_row = execute(
                "SELECT COUNT(*) AS n FROM download_logs WHERE sucesso = FALSE AND created_at >= NOW() - INTERVAL '30 days'",
                fetch="one"
            )
            sucessos = sucessos_row["n"] if sucessos_row else 0
            falhas = falhas_row["n"] if falhas_row else 0
            total_downloads_30d = sucessos + falhas
            download_error_rate = (falhas / total_downloads_30d * 100) if total_downloads_30d > 0 else 0.0
        except Exception:
            total_downloads_30d = 0
            download_error_rate = 0.0

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
            "metrica_conversao_trial": round(trial_conversion_rate, 2),
            "metrica_churn_rate":      round(churn_rate, 2),
            "metrica_ltv_estimado":    round(ltv, 2),
            "metrica_error_rate":      round(download_error_rate, 2),
            "receita_por_plano":        receita_dist,
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
    mes_atual = datetime.now(timezone.utc).strftime("%Y-%m")

    try:
        total = execute(
            f"SELECT COUNT(*) AS n FROM users {where_sql}", tuple(params), fetch="one"
        )["n"]

        rows = execute(
            f"""
            SELECT id, nome, email, plano, cnpj_limite, status,
                   trial_expires_at, vitalicio, acesso_expires_at,
                   plano_origem, is_admin, asaas_customer_id,
                   stripe_customer_id, created_at,
                   (SELECT COUNT(*) FROM clients c
                     WHERE c.user_id = users.id)                       AS clientes_count,
                   COALESCE((SELECT SUM(download_count) FROM monthly_usage m
                              WHERE m.user_id = users.id), 0)          AS downloads_total,
                   COALESCE((SELECT SUM(download_count) FROM monthly_usage m
                              WHERE m.user_id = users.id
                                AND m.mes = %s), 0)                    AS downloads_mes_atual
              FROM users
              {where_sql}
             ORDER BY created_at DESC
             LIMIT %s OFFSET %s
            """,
            tuple([mes_atual] + params + [page_size, offset]),
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
                "tem_stripe":        bool(r["stripe_customer_id"]),
                "created_at":        r["created_at"].isoformat() if r["created_at"] else None,
                "clientes_count":    int(r["clientes_count"] or 0),
                "downloads_total":   int(r["downloads_total"] or 0),
                "downloads_mes_atual": int(r["downloads_mes_atual"] or 0),
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
                   cnpj_limite       = 5,
                   status            = 'cancelado',
                   status_anterior   = NULL,
                   vitalicio         = FALSE,
                   acesso_expires_at = NULL,
                   plano_origem      = 'trial',
                   trial_expires_at  = NULL,
                   cancelled_at      = %s
             WHERE id = %s
            """,
            (now, user_id),
        )
        return jsonify({"ok": True, "msg": "Assinatura removida. Conta voltou ao estado padrão (cancelada/sem plano ativo)."})
    except Exception as exc:
        return jsonify({"error": f"Erro ao excluir assinatura: {exc}"}), 500


# ── POST /api/admin/users/<id>/reativar-gratis ────────────────────────────────
# Reativa manualmente a conta no plano gratuito e LIBERA os 5 CNPJs de novo
# (zera a contagem de uso). Útil para destravar quem já usou os 5 CNPJs grátis
# ou restaurar um lead dormente para um novo teste.

@app.route("/api/admin/users/<user_id>/reativar-gratis", methods=["POST"])
def reativar_gratis(user_id):
    if not _require_admin():
        return jsonify({"error": "Acesso restrito ao administrador."}), 403

    try:
        user = execute("SELECT id, is_admin FROM users WHERE id = %s", (user_id,), fetch="one")
        if not user:
            return jsonify({"error": "Usuário não encontrado."}), 404
        if user["is_admin"]:
            return jsonify({"error": "Não é possível alterar uma conta de administrador."}), 400

        execute(
            """
            UPDATE users
               SET plano             = 'trial',
                   cnpj_limite       = 5,
                   status            = 'ativo',
                   status_anterior   = NULL,
                   vitalicio         = FALSE,
                   acesso_expires_at = NULL,
                   plano_origem      = 'trial',
                   trial_locked_cnpj = NULL,
                   trial_expires_at  = NULL,
                   cancelled_at      = NULL
             WHERE id = %s
            """,
            (user_id,),
        )
        # Zera a contagem de CNPJs usados (libera os 5 gratuitos novamente).
        execute("DELETE FROM monthly_usage WHERE user_id = %s", (user_id,))

        return jsonify({"ok": True, "msg": "Conta reativada no plano gratuito — 5 CNPJs liberados novamente."})
    except Exception as exc:
        return jsonify({"error": f"Erro ao reativar conta: {exc}"}), 500


# ── GET /api/admin/export ─────────────────────────────────────────────────────
# Exporta a lista de cadastros em CSV (nome, e-mail, telefone, etc.).
# ⚠️ Contém PII (telefone/CPF) — restrito a admin.

@app.route("/api/admin/export", methods=["GET"])
def export_cadastros():
    if not _require_admin():
        return jsonify({"error": "Acesso restrito ao administrador."}), 403

    # Protege contra CSV/formula injection: célula que começa com = + - @ (ou
    # tab/CR) pode virar fórmula no Excel. Prefixamos com aspa simples.
    def _safe(value) -> str:
        s = "" if value is None else str(value)
        if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
            return "'" + s
        return s

    try:
        mes_atual = datetime.now(timezone.utc).strftime("%Y-%m")
        rows = execute(
            """
            SELECT id, nome, email, telefone_encrypted,
                   plano, status, plano_origem, cnpj_limite, created_at,
                   (SELECT COUNT(*) FROM clients c
                     WHERE c.user_id = users.id)                       AS clientes,
                   COALESCE((SELECT SUM(download_count) FROM monthly_usage m
                              WHERE m.user_id = users.id), 0)          AS downloads_total,
                   COALESCE((SELECT SUM(download_count) FROM monthly_usage m
                              WHERE m.user_id = users.id
                                AND m.mes = %s), 0)                    AS downloads_mes
              FROM users
             ORDER BY created_at DESC
            """,
            (mes_atual,),
            fetch="all",
        )

        buf = io.StringIO()
        writer = csv.writer(buf)
        # CPF não é exportado — irrelevante para o negócio e reduz exposição de PII.
        writer.writerow([
            "nome", "email", "telefone", "plano", "status",
            "plano_origem", "cnpj_limite", "criado_em",
            "clientes", "downloads_total", "downloads_mes",
        ])
        for r in (rows or []):
            writer.writerow([
                _safe(r["nome"]),
                _safe(r["email"]),
                _safe(decrypt(r["telefone_encrypted"]) or ""),
                r["plano"],
                r["status"],
                r["plano_origem"],
                r["cnpj_limite"],
                r["created_at"].strftime("%Y-%m-%d %H:%M") if r["created_at"] else "",
                int(r["clientes"] or 0),
                int(r["downloads_total"] or 0),
                int(r["downloads_mes"] or 0),
            ])

        # BOM (﻿) para o Excel reconhecer UTF-8 (acentos) corretamente.
        data = "﻿" + buf.getvalue()
        fname = f"cadastros_geradorxml_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
        return Response(
            data,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    except Exception as exc:
        return jsonify({"error": f"Erro ao exportar cadastros: {exc}"}), 500


# ── POST /api/admin/reengajar ─────────────────────────────────────────────────
# Envia um e-mail de reengajamento (welcome | reminder | winback) para uma
# LISTA de usuários selecionados manualmente no painel. Registra em
# engagement_messages para não duplicar. Máx. 100 por chamada (limite de envio).

_REENG_FNS = {
    "welcome":  send_engagement_welcome,
    "reminder": send_engagement_reminder,
    "winback":  send_engagement_winback,
}


@app.route("/api/admin/reengajar", methods=["POST"])
def reengajar():
    if not _require_admin():
        return jsonify({"error": "Acesso restrito ao administrador."}), 403

    data = request.get_json(silent=True) or {}
    tipo = (data.get("tipo") or "").strip().lower()
    ids  = data.get("user_ids") or []

    if tipo not in _REENG_FNS:
        return jsonify({"error": "Tipo de e-mail inválido."}), 400
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "Selecione ao menos um usuário."}), 400
    if len(ids) > 100:
        return jsonify({"error": "Selecione no máximo 100 usuários por vez (limite de envio)."}), 400

    # Garante a tabela de controle (mesmo padrão do cron).
    try:
        execute(
            """
            CREATE TABLE IF NOT EXISTS engagement_messages (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                tipo TEXT NOT NULL,
                sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, tipo)
            )
            """
        )
    except Exception:
        pass

    enviar = _REENG_FNS[tipo]
    enviados, falhas = 0, 0
    for uid in ids:
        try:
            row = execute("SELECT id, nome, email FROM users WHERE id = %s", (str(uid),), fetch="one")
            if not row or not row.get("email"):
                falhas += 1
                continue
            if enviar(row["email"], row["nome"]):
                execute(
                    "INSERT INTO engagement_messages (user_id, tipo) VALUES (%s, %s) "
                    "ON CONFLICT (user_id, tipo) DO NOTHING",
                    (row["id"], tipo),
                )
                enviados += 1
            else:
                falhas += 1
        except Exception:
            falhas += 1

    return jsonify({"ok": True, "enviados": enviados, "falhas": falhas})


# ── Integrations: Meta Conversions API ────────────────────────────────────────
# GET   /api/admin/integrations/meta       — current config + token status
# POST  /api/admin/integrations/meta       — update pixel id / toggles / test code
# POST  /api/admin/integrations/meta/test  — send a test "Lead" event to Meta
#
# IMPORTANT: the access token is a server-side secret (META_CAPI_ACCESS_TOKEN
# env var) — it is NEVER read from or written to the request/response here.

@app.route("/api/admin/integrations/meta", methods=["GET"])
def meta_capi_get():
    if not _require_admin():
        return jsonify({"error": "Acesso restrito ao administrador."}), 403
    try:
        settings = meta_capi.get_settings()
        return jsonify({
            "settings":         settings,
            "token_configured": meta_capi.token_configured(),
            "active":           meta_capi.is_active(),
        })
    except Exception as exc:
        return jsonify({"error": f"Erro ao carregar integração: {exc}"}), 500


@app.route("/api/admin/integrations/meta", methods=["POST"])
def meta_capi_update():
    if not _require_admin():
        return jsonify({"error": "Acesso restrito ao administrador."}), 403

    data = request.get_json(silent=True) or {}
    pixel_id = data.get("pixel_id")
    if pixel_id is not None:
        pixel_id = str(pixel_id).strip()
        if pixel_id and not pixel_id.isdigit():
            return jsonify({"error": "Pixel ID deve conter apenas números."}), 400

    try:
        settings = meta_capi.save_settings(
            enabled=data.get("enabled"),
            pixel_id=pixel_id,
            test_event_code=data.get("test_event_code"),
            events=data.get("events") if isinstance(data.get("events"), dict) else None,
        )
        return jsonify({
            "ok":               True,
            "settings":         settings,
            "token_configured": meta_capi.token_configured(),
            "active":           meta_capi.is_active(),
        })
    except Exception as exc:
        return jsonify({"error": f"Erro ao salvar integração: {exc}"}), 500


@app.route("/api/admin/integrations/meta/test", methods=["POST"])
def meta_capi_send_test():
    if not _require_admin():
        return jsonify({"error": "Acesso restrito ao administrador."}), 403

    if not meta_capi.token_configured():
        return jsonify({"error": "META_CAPI_ACCESS_TOKEN não configurado no servidor. "
                                  "Adicione a variável de ambiente e implante novamente."}), 400
    settings = meta_capi.get_settings()
    if not settings.get("pixel_id"):
        return jsonify({"error": "Informe e salve o Pixel ID antes de enviar um evento de teste."}), 400

    try:
        result = meta_capi.send_test_event()
        if result.get("ok"):
            return jsonify({"ok": True, "msg": "Evento de teste enviado ao Meta com sucesso. "
                                                 "Verifique em Eventos de Teste no Gerenciador de Eventos.",
                            "response": result.get("body")})
        return jsonify({"error": "Meta recusou o evento de teste.",
                        "details": result.get("body") or result.get("skipped")}), 502
    except Exception as exc:
        return jsonify({"error": f"Erro ao enviar evento de teste: {exc}"}), 500

"""
/api/webhook/asaas — ASAAS payment webhook handler

Configure no painel ASAAS:
  URL: https://<seu-dominio>/api/webhook/asaas
  Header de autenticação: asaas-access-token = <ASAAS_WEBHOOK_TOKEN>

Variáveis de ambiente necessárias:
  ASAAS_WEBHOOK_TOKEN   — token que o ASAAS envia no header para validação
"""
import sys, os
from datetime import datetime, timezone
from flask import Flask, request, jsonify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.db import execute

app = Flask(__name__)

WEBHOOK_TOKEN = os.environ.get("ASAAS_WEBHOOK_TOKEN", "")

# Map keywords in subscription description → internal plan name
_PLANO_MAP: dict[str, str] = {
    "bpo":     "bpo",
    "office":  "office",
    "pro":     "pro",
    "starter": "starter",
}

_LIMITES: dict[str, int] = {
    "starter": 10,
    "pro":     50,
    "office":  150,
    "bpo":     -1,
}


@app.route("/api/webhook/asaas", methods=["POST"])
def webhook():
    # Validate webhook token (if configured)
    if WEBHOOK_TOKEN:
        received = request.headers.get("asaas-access-token", "")
        if received != WEBHOOK_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401

    data         = request.get_json(silent=True) or {}
    event        = data.get("event", "")
    payment      = data.get("payment")      or {}
    subscription = data.get("subscription") or {}

    # Extract ASAAS customer ID from various event shapes
    customer_id = (
        payment.get("customer")
        or subscription.get("customer")
        or data.get("customer", "")
    )

    if not customer_id:
        return jsonify({"ok": True, "msg": "no customer id"}), 200

    user = execute(
        "SELECT id, plano FROM users WHERE asaas_customer_id = %s",
        (customer_id,),
        fetch="one",
    )
    if not user:
        # Unknown customer — store for future reconciliation
        return jsonify({"ok": True, "msg": "user not found"}), 200

    user_id = str(user["id"])
    now     = datetime.now(timezone.utc)

    # ── Payment confirmed → activate / upgrade plan ────────────────────────
    if event in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
        desc = (
            subscription.get("description", "")
            or payment.get("description", "")
            or ""
        ).lower()

        # Detect plan from description keywords (longest match wins)
        plano = "starter"
        for key in ("bpo", "office", "pro", "starter"):
            if key in desc:
                plano = _PLANO_MAP[key]
                break

        limite = _LIMITES.get(plano, 10)

        execute(
            """
            UPDATE users
               SET plano = %s,
                   cnpj_limite = %s,
                   status = 'ativo',
                   trial_expires_at = NULL,
                   plano_origem = 'asaas',
                   vitalicio = FALSE,
                   acesso_expires_at = NULL
             WHERE id = %s
            """,
            (plano, limite, user_id),
        )

    # ── Payment overdue → suspend ──────────────────────────────────────────
    elif event == "PAYMENT_OVERDUE":
        execute("UPDATE users SET status = 'suspenso' WHERE id = %s", (user_id,))

    # ── Subscription / payment cancelled ──────────────────────────────────
    elif event in ("SUBSCRIPTION_DELETED", "PAYMENT_DELETED"):
        execute("UPDATE users SET status = 'cancelado' WHERE id = %s", (user_id,))

    # ── Subscription reactivated ───────────────────────────────────────────
    elif event == "PAYMENT_RESTORED":
        execute("UPDATE users SET status = 'ativo' WHERE id = %s", (user_id,))

    return jsonify({"ok": True, "event": event}), 200

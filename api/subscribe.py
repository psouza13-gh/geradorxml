"""
/api/subscribe — start a paid subscription via ASAAS (Vercel serverless)

  POST /api/subscribe
    body: { "plano": "starter"|"pro"|"office"|"bpo",
            "cpfCnpj": "00000000000",
            "billingType": "UNDEFINED"|"BOLETO"|"CREDIT_CARD"|"PIX" }
    → 201 { "checkout_url": "https://...", "subscription_id": "sub_..." }

Requires JWT auth (Authorization: Bearer <token>).

Flow:
  1. Reuse the user's ASAAS customer (asaas_customer_id) or create one.
  2. Create a recurring subscription for the chosen plan.
  3. Return the hosted checkout link (invoiceUrl) of the first invoice —
     the user is redirected there to pick a payment method and pay.
  4. When ASAAS confirms payment, /api/webhook/asaas activates the plan.
"""
import sys, os, re
from flask import Flask, request, jsonify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.auth_service import verify_token, get_user_by_id
from app.services.db import execute
from app.services import asaas_service
from app.services.asaas_service import AsaasError

app = Flask(__name__)

_CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
}


@app.after_request
def _cors(resp):
    for k, v in _CORS.items():
        resp.headers[k] = v
    return resp


# Plan catalog — keep in sync with app/services/subscription_service.PLANO_LIMITES
# and the public pricing table (public/index.html).
_PLANOS = {
    "starter": {"valor": 97.0,  "nome": "Starter"},
    "pro":     {"valor": 297.0, "nome": "Pro"},
    "office":  {"valor": 597.0, "nome": "Office"},
    "bpo":     {"valor": 997.0, "nome": "BPO"},
}

_BILLING_TYPES = {"UNDEFINED", "BOLETO", "CREDIT_CARD", "PIX"}

_CPF_RE  = re.compile(r"^\d{11}$")
_CNPJ_RE = re.compile(r"^\d{14}$")


def _get_auth_payload():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return verify_token(auth[7:])


@app.route("/api/subscribe", methods=["OPTIONS"])
def preflight():
    return app.response_class("", 204)


@app.route("/api/subscribe", methods=["POST"])
def subscribe():
    payload = _get_auth_payload()
    if not payload:
        return jsonify({"error": "Não autenticado."}), 401

    if not asaas_service.is_configured():
        return jsonify({"error": "Pagamentos temporariamente indisponíveis. Tente novamente em instantes."}), 503

    data         = request.get_json(silent=True) or {}
    plano        = (data.get("plano") or "").strip().lower()
    cpf_cnpj     = re.sub(r"\D", "", data.get("cpfCnpj") or "")
    billing_type = (data.get("billingType") or "UNDEFINED").strip().upper()

    if plano not in _PLANOS:
        return jsonify({"error": "Plano inválido."}), 400
    if not (_CPF_RE.match(cpf_cnpj) or _CNPJ_RE.match(cpf_cnpj)):
        return jsonify({"error": "CPF ou CNPJ inválido. Informe apenas números (11 ou 14 dígitos)."}), 400
    if billing_type not in _BILLING_TYPES:
        return jsonify({"error": "Forma de pagamento inválida."}), 400

    try:
        user = get_user_by_id(payload["sub"])
    except Exception:
        return jsonify({"error": "Erro interno. Tente novamente."}), 500

    if not user:
        return jsonify({"error": "Usuário não encontrado."}), 404

    plano_info = _PLANOS[plano]

    try:
        # 1) Reuse or create the ASAAS customer
        customer_id = user.get("asaas_customer_id")
        if not customer_id:
            customer = asaas_service.create_customer(
                nome=user["nome"],
                email=user["email"],
                cpf_cnpj=cpf_cnpj,
                external_reference=str(user["id"]),
            )
            customer_id = customer["id"]
            execute(
                "UPDATE users SET asaas_customer_id = %s WHERE id = %s",
                (customer_id, user["id"]),
            )

        # 2) Create the recurring subscription (first charge due today)
        subscription = asaas_service.create_subscription(
            customer_id=customer_id,
            value=plano_info["valor"],
            description=f"geradorxml — Plano {plano_info['nome']}",
            billing_type=billing_type,
        )

        # 3) Hosted checkout link for the first invoice
        checkout_url = asaas_service.get_first_invoice_url(subscription["id"])
        if not checkout_url:
            return jsonify({"error": "Assinatura criada, mas o link de pagamento ainda não está disponível. "
                                      "Atualize a página em alguns segundos."}), 202

        return jsonify({
            "checkout_url":    checkout_url,
            "subscription_id": subscription["id"],
        }), 201

    except AsaasError as exc:
        return jsonify({"error": f"Não foi possível iniciar a assinatura: {exc}"}), 502
    except Exception:
        return jsonify({"error": "Erro interno. Tente novamente."}), 500

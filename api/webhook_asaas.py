"""
ASAAS webhook — DESATIVADO.

O gateway ASAAS foi removido em junho/2025.
Todos os pagamentos são agora processados via Stripe.
Webhook ativo: /api/webhook/stripe
"""
from flask import Flask, jsonify

app = Flask(__name__)


@app.route("/api/webhook/asaas", methods=["POST", "GET"])
def webhook():
    return jsonify({"error": "ASAAS desativado. Use /api/webhook/stripe."}), 410

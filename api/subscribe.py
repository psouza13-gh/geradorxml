"""
/api/subscribe — start a paid subscription via Stripe Checkout (Vercel serverless)

  POST /api/subscribe
    body: { "plano": "starter"|"pro"|"office"|"bpo" }
    → 201 { "checkout_url": "https://checkout.stripe.com/..." }

Requires JWT auth (Authorization: Bearer <token>).

Flow:
  1. Validate JWT + plano.
  2. Create a Stripe Checkout Session (subscription mode, card + boleto).
  3. Return the hosted checkout URL — frontend redirects the user there.
  4. After payment, Stripe sends a webhook to /api/webhook/stripe which
     activates the plan in the database automatically.
"""
import sys, os
from flask import Flask, request, jsonify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.auth_service import verify_token, get_user_by_id
from app.services import stripe_service
from app.services.stripe_service import StripeError

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


# Valid plan identifiers — keep in sync with stripe_service._PRICE_IDS
# and subscription_service.PLANO_LIMITES
_PLANOS = {"starter", "pro", "office", "bpo"}

# Base URL used to build the Stripe success/cancel redirect URLs.
# Override via APP_BASE_URL env var if the domain ever changes.
_BASE_URL = os.environ.get("APP_BASE_URL", "https://geradorxml.vercel.app")


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
    # ── Auth ──────────────────────────────────────────────────────────────
    payload = _get_auth_payload()
    if not payload:
        return jsonify({"error": "Não autenticado."}), 401

    # ── Gateway availability ───────────────────────────────────────────────
    if not stripe_service.is_configured():
        return jsonify({
            "error": "Pagamentos temporariamente indisponíveis. Tente novamente em instantes."
        }), 503

    # ── Input validation ───────────────────────────────────────────────────
    data  = request.get_json(silent=True) or {}
    plano = (data.get("plano") or "").strip().lower()

    if plano not in _PLANOS:
        return jsonify({"error": "Plano inválido."}), 400

    # ── Load user ──────────────────────────────────────────────────────────
    try:
        user = get_user_by_id(payload["sub"])
    except Exception:
        return jsonify({"error": "Erro interno. Tente novamente."}), 500

    if not user:
        return jsonify({"error": "Usuário não encontrado."}), 404

    # ── Create Stripe Checkout Session ────────────────────────────────────
    # success_url: Stripe appends ?session_id={CHECKOUT_SESSION_ID} automatically
    # cancel_url:  user is sent back to /app when they click "back"
    success_url = (
        f"{_BASE_URL}/app?subscription=success"
        "&session_id={CHECKOUT_SESSION_ID}"   # Stripe replaces this placeholder
    )
    cancel_url = f"{_BASE_URL}/app?subscription=cancelled"

    try:
        session = stripe_service.create_checkout_session(
            user_id=str(user["id"]),
            user_email=user["email"],
            plano=plano,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return jsonify({"checkout_url": session["url"]}), 201

    except StripeError as exc:
        return jsonify({"error": f"Não foi possível iniciar a assinatura: {exc}"}), 502
    except Exception:
        return jsonify({"error": "Erro interno. Tente novamente."}), 500

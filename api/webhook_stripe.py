"""
/api/webhook/stripe — Stripe payment webhook handler (Vercel serverless)

Configure no painel Stripe:
  URL:    https://geradorxml.vercel.app/api/webhook/stripe
  Events: checkout.session.completed
          invoice.payment_succeeded
          invoice.payment_failed
          customer.subscription.deleted
          customer.subscription.updated

Environment:
  STRIPE_WEBHOOK_SECRET — signing secret (whsec_...) shown after creating the endpoint
"""
import sys, os, traceback
from datetime import datetime, timezone
from flask import Flask, request, jsonify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import stripe as _stripe
from app.services.db import execute
from app.services.stripe_service import construct_webhook_event, price_to_plano, StripeError
from app.services.meta_capi_service import track_purchase

app = Flask(__name__)


def _log_webhook_error(etype: str, exc: Exception) -> None:
    """
    Best-effort: record the exact exception before re-raising, so a failed
    webhook delivery (which Stripe will retry) becomes diagnosable instead of
    a silent 500. Never lets a logging failure mask the original error.
    """
    try:
        execute(
            """
            CREATE TABLE IF NOT EXISTS webhook_errors (
                id         BIGSERIAL PRIMARY KEY,
                origem     TEXT NOT NULL,
                evento     TEXT,
                erro       TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        execute(
            "INSERT INTO webhook_errors (origem, evento, erro) VALUES (%s, %s, %s)",
            ("stripe", etype, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-2000:]}"),
        )
    except Exception:
        pass

# ── Plan limits (keep in sync with subscription_service.PLANO_LIMITES) ────────
_LIMITES: dict[str, int] = {
    "starter": 15,
    "pro":     50,
    "office":  150,
    "bpo":     -1,
}

# Monthly values in BRL — reported to Meta Ads Conversions API on first purchase
_VALORES: dict[str, float] = {
    "starter": 67.0,
    "pro":     147.0,
    "office":  297.0,
    "bpo":     597.0,
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _activate_plan(
    user_id: str,
    plano: str,
    stripe_customer_id: str,
    stripe_subscription_id: str,
) -> None:
    """
    Activate (or keep active) a Stripe-backed paid plan for a user.
    Clears all trial state and marks the subscription origin as 'stripe'.
    """
    limite = _LIMITES.get(plano, 10)
    execute(
        """
        UPDATE users
           SET plano                  = %s,
               cnpj_limite            = %s,
               status                 = 'ativo',
               trial_expires_at       = NULL,
               trial_locked_cnpj      = NULL,
               plano_origem           = 'stripe',
               vitalicio              = FALSE,
               acesso_expires_at      = NULL,
               stripe_customer_id     = %s,
               stripe_subscription_id = %s,
               cancelled_at           = NULL
         WHERE id = %s
        """,
        (plano, limite, stripe_customer_id, stripe_subscription_id, user_id),
    )


def _find_user_by_stripe_customer(stripe_customer_id: str) -> dict | None:
    return execute(
        "SELECT id, email, plano FROM users WHERE stripe_customer_id = %s",
        (stripe_customer_id,),
        fetch="one",
    )


def _plano_from_invoice(invoice_data: dict) -> str | None:
    """Try to detect the plan from an invoice's line items via Price ID."""
    lines = (invoice_data.get("lines") or {}).get("data", [])
    if lines:
        price_id = (lines[0].get("price") or {}).get("id")
        if price_id:
            return price_to_plano(price_id)
    return None


def _plano_from_subscription(sub_data: dict) -> str | None:
    """Try to detect the plan from a subscription's items via Price ID."""
    items = (sub_data.get("items") or {}).get("data", [])
    if items:
        price_id = (items[0].get("price") or {}).get("id")
        if price_id:
            return price_to_plano(price_id)
    return None


# ── Webhook endpoint ──────────────────────────────────────────────────────────

@app.route("/api/webhook/stripe", methods=["POST"])
def webhook():
    # ── Validate Stripe signature ─────────────────────────────────────────
    payload    = request.get_data()          # must be raw bytes, not parsed
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = construct_webhook_event(payload, sig_header)
    except _stripe.SignatureVerificationError:
        return jsonify({"error": "Invalid webhook signature"}), 400
    except StripeError as exc:
        return jsonify({"error": str(exc)}), 400

    etype = event["type"]
    data  = event["data"]["object"]

    # Wrap the whole dispatch in try/except so a real bug is LOGGED before
    # the 500 propagates (Stripe still retries — correct for a payment
    # webhook — but the exact exception is now captured for diagnosis
    # instead of vanishing into an opaque 500).
    try:
        # ══ checkout.session.completed ════════════════════════════════════
        # Fired once when a user completes the Stripe Checkout flow and pays.
        # This is the primary activation event.
        if etype == "checkout.session.completed":
            meta            = data.get("metadata") or {}
            user_id         = meta.get("user_id")
            plano           = (meta.get("plano") or "starter").lower()
            stripe_customer = data.get("customer")  or ""
            stripe_sub      = data.get("subscription") or ""

            if not user_id or not stripe_customer:
                return jsonify({"ok": True, "msg": "missing metadata — skipped"}), 200

            user = execute(
                "SELECT id, email, plano FROM users WHERE id = %s",
                (user_id,),
                fetch="one",
            )
            if not user:
                return jsonify({"ok": True, "msg": "user not found"}), 200

            plano_anterior = (user.get("plano") or "trial").lower()
            _activate_plan(user_id, plano, stripe_customer, stripe_sub)

            # Fire Meta Ads Purchase event only on the FIRST paid activation
            # (trial → paid), not on subsequent renewals or upgrades, to avoid
            # duplicate "Purchase" signals for the same customer.
            if plano_anterior == "trial":
                try:
                    track_purchase(
                        user_id=user_id,
                        email=user.get("email"),
                        plano=plano,
                        value=_VALORES.get(plano, 0.0),
                        event_id_suffix=str(data.get("id") or ""),
                    )
                except Exception:
                    pass  # Never block the webhook response on tracking errors

        # ══ invoice.payment_succeeded ═══════════════════════════════════════
        # Fired on each successful monthly renewal charge.
        # Ensures the account stays active even if the webhook for the original
        # checkout was missed or retried.
        elif etype == "invoice.payment_succeeded":
            stripe_customer = data.get("customer") or ""
            stripe_sub      = data.get("subscription") or ""

            if not stripe_customer:
                return jsonify({"ok": True}), 200

            user = _find_user_by_stripe_customer(stripe_customer)
            if not user:
                return jsonify({"ok": True, "msg": "user not found"}), 200

            # Detect current plan from the invoice's Price ID (handles upgrades)
            plano = _plano_from_invoice(data) or (user.get("plano") or "starter").lower()
            _activate_plan(str(user["id"]), plano, stripe_customer, stripe_sub)

        # ══ invoice.payment_failed ══════════════════════════════════════════
        # Fired when a renewal charge fails (e.g., expired card).
        # Suspends the account — user keeps data but can't download.
        elif etype == "invoice.payment_failed":
            stripe_customer = data.get("customer") or ""
            if stripe_customer:
                execute(
                    "UPDATE users SET status = 'suspenso' WHERE stripe_customer_id = %s",
                    (stripe_customer,),
                )

        # ══ customer.subscription.deleted ═══════════════════════════════════
        # Fired when a subscription is cancelled (by user via Customer Portal
        # or by admin in the Stripe Dashboard).
        elif etype == "customer.subscription.deleted":
            stripe_customer = data.get("customer") or ""
            if stripe_customer:
                execute(
                    "UPDATE users SET status = 'cancelado', cancelled_at = NOW() WHERE stripe_customer_id = %s",
                    (stripe_customer,),
                )

        # ══ customer.subscription.updated ═══════════════════════════════════
        # Fired on plan upgrades/downgrades via Stripe Customer Portal.
        elif etype == "customer.subscription.updated":
            stripe_customer = data.get("customer") or ""
            stripe_sub      = data.get("id") or ""

            if not stripe_customer:
                return jsonify({"ok": True}), 200

            user = _find_user_by_stripe_customer(stripe_customer)
            if not user:
                return jsonify({"ok": True}), 200

            plano = _plano_from_subscription(data)
            if plano:
                _activate_plan(str(user["id"]), plano, stripe_customer, stripe_sub)

    except Exception as exc:
        _log_webhook_error(etype, exc)
        raise

    return jsonify({"ok": True, "event": etype}), 200

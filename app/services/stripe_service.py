"""
Stripe payment gateway integration.

Docs: https://stripe.com/docs/api

Environment variables (set in Vercel):
  STRIPE_SECRET_KEY     — API secret key (sk_live_... or sk_test_...)
  STRIPE_WEBHOOK_SECRET — Webhook signing secret (whsec_...)
  STRIPE_PRICE_STARTER  — Price ID for Starter plan  (price_...)
  STRIPE_PRICE_PRO      — Price ID for Pro plan       (price_...)
  STRIPE_PRICE_OFFICE   — Price ID for Office plan    (price_...)
  STRIPE_PRICE_BPO      — Price ID for BPO plan       (price_...)
"""
import os
import stripe as _stripe

# ── Configuration ─────────────────────────────────────────────────────────────

STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Internal plan name → Stripe Price ID
_PRICE_IDS: dict[str, str] = {
    "starter": os.environ.get("STRIPE_PRICE_STARTER", ""),
    "pro":     os.environ.get("STRIPE_PRICE_PRO",     ""),
    "office":  os.environ.get("STRIPE_PRICE_OFFICE",  ""),
    "bpo":     os.environ.get("STRIPE_PRICE_BPO",     ""),
}

if STRIPE_SECRET_KEY:
    _stripe.api_key = STRIPE_SECRET_KEY


# ── Error class ───────────────────────────────────────────────────────────────

class StripeError(Exception):
    """Raised when a Stripe API call fails or configuration is missing."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_configured() -> bool:
    """Return True if the Stripe secret key is set."""
    return bool(STRIPE_SECRET_KEY)


def get_price_id(plano: str) -> str | None:
    """Return the Stripe Price ID for an internal plan name, or None if not set."""
    return _PRICE_IDS.get(plano) or None


def price_to_plano(price_id: str) -> str | None:
    """Reverse-map a Stripe Price ID back to an internal plan name."""
    for plano, pid in _PRICE_IDS.items():
        if pid and pid == price_id:
            return plano
    return None


# ── Checkout Session ──────────────────────────────────────────────────────────

def create_checkout_session(
    *,
    user_id: str,
    user_email: str,
    plano: str,
    success_url: str,
    cancel_url: str,
) -> dict:
    """
    Create a Stripe Checkout Session in subscription mode.

    Payment methods: card + boleto (both supported natively in Brazil).
    The session URL (session["url"]) is the Stripe-hosted checkout page.

    Metadata (user_id, plano) is attached to both the session and the
    subscription object so the webhook can identify the user on any event.

    Returns:
        {"url": "<checkout url>", "id": "<session id>"}
    """
    if not STRIPE_SECRET_KEY:
        raise StripeError("Pagamentos temporariamente indisponíveis (Stripe não configurado).")

    price_id = get_price_id(plano)
    if not price_id:
        raise StripeError(
            f"Price ID não configurado para o plano '{plano}'. "
            "Verifique as variáveis de ambiente STRIPE_PRICE_*."
        )

    try:
        session = _stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            customer_email=user_email,
            success_url=success_url,
            cancel_url=cancel_url,
            # Store our internal identifiers on both session + subscription
            metadata={"user_id": user_id, "plano": plano},
            subscription_data={
                "metadata": {"user_id": user_id, "plano": plano},
            },
            # Card (auto-charge, recurrence) + Boleto (manual, monthly)
            payment_method_types=["card", "boleto"],
            locale="pt-BR",
            allow_promotion_codes=True,
        )
        return {"url": session.url, "id": session.id}

    except _stripe.StripeError as exc:
        raise StripeError(str(exc)) from exc


# ── Webhook signature validation ──────────────────────────────────────────────

def construct_webhook_event(payload: bytes, sig_header: str):
    """
    Validate the Stripe-Signature header and parse the event payload.

    Raises:
        stripe.SignatureVerificationError  — if the signature is invalid
        StripeError                        — if STRIPE_WEBHOOK_SECRET is not set
    """
    if not STRIPE_WEBHOOK_SECRET:
        raise StripeError("STRIPE_WEBHOOK_SECRET não configurado.")
    # Raises stripe.SignatureVerificationError on mismatch — let caller handle it
    return _stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)

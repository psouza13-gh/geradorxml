import os
from flask import Flask, jsonify

app = Flask(__name__)


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/health/stripe")
def health_stripe():
    """
    Diagnostic endpoint — reports which Stripe env vars are present.
    Returns presence (True/False) and a redacted prefix of each value.
    Does NOT expose the full values.
    """
    def _check(var: str) -> dict:
        val = os.environ.get(var, "")
        present = bool(val)
        prefix = val[:8] + "..." if len(val) > 8 else ("(empty)" if not val else val)
        return {"present": present, "prefix": prefix}

    return jsonify({
        "STRIPE_SECRET_KEY":     _check("STRIPE_SECRET_KEY"),
        "STRIPE_WEBHOOK_SECRET": _check("STRIPE_WEBHOOK_SECRET"),
        "STRIPE_PRICE_STARTER":  _check("STRIPE_PRICE_STARTER"),
        "STRIPE_PRICE_PRO":      _check("STRIPE_PRICE_PRO"),
        "STRIPE_PRICE_OFFICE":   _check("STRIPE_PRICE_OFFICE"),
        "STRIPE_PRICE_BPO":      _check("STRIPE_PRICE_BPO"),
    })

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flask import Flask, request, jsonify

app = Flask(__name__)


def _require_admin_key() -> bool:
    """Guard for sensitive health endpoints: require X-Admin-Key header."""
    key = os.environ.get("ADMIN_HEALTH_KEY", "")
    if not key:
        return False
    return request.headers.get("X-Admin-Key", "") == key


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/health/stripe")
def health_stripe():
    """
    Diagnostic endpoint — reports which Stripe env vars are present.
    Requires X-Admin-Key header (ADMIN_HEALTH_KEY env var).
    """
    if not _require_admin_key():
        return jsonify({"error": "Unauthorized"}), 401

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


@app.route("/api/health/migrate")
def health_migrate():
    """
    Run migration_metrics.sql against the database.
    Requires X-Admin-Key header (ADMIN_HEALTH_KEY env var).
    """
    if not _require_admin_key():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from app.services.db import execute
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        migration_path = os.path.join(root, "migration_metrics.sql")

        with open(migration_path, "r", encoding="utf-8") as f:
            sql = f.read()

        execute(sql)
        return jsonify({"status": "migration completed successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


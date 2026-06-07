"""
/api/public — small read-only endpoints safe to expose WITHOUT authentication.

  GET /api/public/meta-pixel  — returns the Meta Pixel ID + enabled flag so the
                                 client-side (browser) Meta Pixel snippet can be
                                 loaded dynamically from the public pages.

Security note:
  The Pixel/Dataset ID is NOT a secret — it is always visible in the page
  source of any site that uses the Meta Pixel (it travels in every client-side
  `fbq('init', <id>)` call and every network request to facebook.net). The
  long-lived ACCESS TOKEN used for server-side Conversions API calls is never
  exposed here (or anywhere on the frontend) — it lives only in the
  META_CAPI_ACCESS_TOKEN environment variable (see app/services/meta_capi_service.py).
"""
import sys, os
from flask import Flask, jsonify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services import meta_capi_service as meta_capi

app = Flask(__name__)


@app.route("/api/public/meta-pixel", methods=["GET"])
def meta_pixel_config():
    try:
        settings = meta_capi.get_settings()
        return jsonify({
            "enabled":  bool(settings.get("enabled")),
            "pixel_id": settings.get("pixel_id") or "",
        })
    except Exception:
        # Never break page load because of this — just report "not configured".
        return jsonify({"enabled": False, "pixel_id": ""})

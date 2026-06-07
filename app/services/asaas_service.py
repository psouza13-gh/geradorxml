"""
ASAAS payment gateway integration.

Docs: https://docs.asaas.com/
  - Create customer:      POST /v3/customers
  - Create subscription:  POST /v3/subscriptions
  - List payments:        GET  /v3/payments?subscription={id}

Environment:
  ASAAS_API_KEY — API key from the ASAAS dashboard
                  (Integrações → Chaves de API → Gerar Chave de API).
                  Production keys start with "$aact_prod_",
                  Sandbox keys start with "$aact_hmlg_".
                  The base URL is selected automatically from the key prefix.
"""
import os
from datetime import datetime, timezone

import requests

ASAAS_API_KEY = os.environ.get("ASAAS_API_KEY", "")

_PROD_BASE    = "https://api.asaas.com/v3"
_SANDBOX_BASE = "https://api-sandbox.asaas.com/v3"


class AsaasError(Exception):
    """Raised when the ASAAS API call fails or returns an error response."""

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def _base_url() -> str:
    return _PROD_BASE if ASAAS_API_KEY.startswith("$aact_prod_") else _SANDBOX_BASE


def is_configured() -> bool:
    return bool(ASAAS_API_KEY)


def _headers() -> dict:
    return {
        "access_token": ASAAS_API_KEY,
        "Content-Type": "application/json",
        "User-Agent":   "geradorxml/1.0 (+https://geradorxml.vercel.app)",
    }


def _request(method: str, path: str, **kwargs):
    if not ASAAS_API_KEY:
        raise AsaasError("Pagamentos temporariamente indisponíveis (ASAAS não configurado).")

    url = f"{_base_url()}{path}"
    try:
        resp = requests.request(method, url, headers=_headers(), timeout=30, **kwargs)
    except requests.RequestException as exc:
        raise AsaasError(f"Falha de conexão com o gateway de pagamento: {exc}") from exc

    if resp.status_code >= 400:
        msg, payload = resp.text, None
        try:
            payload = resp.json()
            errors = payload.get("errors") or []
            if errors:
                msg = "; ".join(e.get("description", "") for e in errors if e.get("description"))
        except Exception:
            pass
        raise AsaasError(msg or "Erro ao comunicar com o gateway de pagamento.",
                         status_code=resp.status_code, payload=payload)

    try:
        return resp.json()
    except ValueError:
        return {}


# ── Customers ─────────────────────────────────────────────────────────────────

def create_customer(nome: str, email: str, cpf_cnpj: str, external_reference: str | None = None) -> dict:
    """
    Create a customer on ASAAS.
    Required fields per ASAAS docs: name, cpfCnpj.
    Returns the created customer dict (includes 'id', e.g. "cus_000005219613").
    """
    body = {
        "name":    nome,
        "email":   email,
        "cpfCnpj": cpf_cnpj,
    }
    if external_reference:
        body["externalReference"] = external_reference
    return _request("POST", "/customers", json=body)


# ── Subscriptions ─────────────────────────────────────────────────────────────

def create_subscription(customer_id: str, value: float, description: str,
                         billing_type: str = "UNDEFINED", cycle: str = "MONTHLY") -> dict:
    """
    Create a recurring subscription. The first charge is generated immediately
    with due date = today (UTC), per ASAAS behavior for nextDueDate = today.

    billing_type: "UNDEFINED" (customer chooses on checkout), "BOLETO",
                  "CREDIT_CARD" or "PIX".
    """
    body = {
        "customer":    customer_id,
        "billingType": billing_type,
        "value":       value,
        "nextDueDate": datetime.now(timezone.utc).date().isoformat(),
        "cycle":       cycle,
        "description": description,
    }
    return _request("POST", "/subscriptions", json=body)


def get_first_invoice_url(subscription_id: str) -> str | None:
    """
    Fetch the first payment generated for a subscription and return its
    hosted checkout link (`invoiceUrl`) — the page where the customer picks
    a payment method (card/boleto/PIX) and pays.
    """
    data = _request("GET", "/payments", params={"subscription": subscription_id, "limit": 1})
    items = data.get("data") or []
    if not items:
        return None
    return items[0].get("invoiceUrl")

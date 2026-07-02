"""
Consulta pública best-effort de CNPJ (BrasilAPI) — usada só para AUTO-PREENCHER
o nome da empresa na tela de confirmação de cadastro de cliente, como reforço
visual de conferência.

Nunca bloqueia o fluxo: se a API estiver fora do ar, lenta, ou o CNPJ não for
encontrado, retorna None silenciosamente e o usuário confirma manualmente.
"""
import requests

BRASILAPI_URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"


def lookup_cnpj(cnpj_digits: str) -> dict | None:
    """Return {"nome": str, "situacao": str} on success, or None on any failure."""
    try:
        resp = requests.get(BRASILAPI_URL.format(cnpj=cnpj_digits), timeout=5)
        if resp.status_code != 200:
            return None
        data = resp.json()
        nome = data.get("razao_social") or data.get("nome_fantasia") or ""
        if not nome:
            return None
        return {
            "nome": nome,
            "situacao": data.get("descricao_situacao_cadastral") or "",
        }
    except Exception:
        return None

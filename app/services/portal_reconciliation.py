"""
Reconciliação de NFS-e recebidas: portal (consulta) × certificado/ADN (distribuição).

Motivação
---------
A ferramenta baixa NFS-e pela API de Distribuição DF-e do ADN (via certificado A1).
Notas emitidas por municípios de sistema próprio (ex.: São Paulo, Osasco) aparecem
na CONSULTA do portal, mas nem sempre são DISTRIBUÍDAS ao tomador via DF-e — logo
não chegam pela ferramenta, mesmo constando no portal.

Este módulo autentica no portal usando o MESMO certificado (login por certificado no
host dedicado certificado.nfse.gov.br, que é mTLS), LISTA todas as recebidas do período
(a listagem NÃO é protegida por captcha; só o download é) e compara com o que já foi
baixado — sinalizando as notas faltantes, sem baixá-las.

Nunca deve derrubar o fluxo de download: o chamador deve tratar exceções e seguir.
"""

import re
import time
from datetime import date
from typing import Callable, Dict, List

import requests

from app.services.cert_handler import export_to_pem_files

PORTAL = "https://www.nfse.gov.br"
CERT_HOST = "https://certificado.nfse.gov.br"
CERT_LOGIN_URL = f"{CERT_HOST}/EmissorNacional/Certificado"
RECEBIDAS_URL = f"{PORTAL}/EmissorNacional/Notas/Recebidas"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

MAX_PAGINAS = 200  # trava de segurança (~3000 notas)

# Regex do link de download que identifica cada NFS-e na listagem.
_RE_CHAVE = re.compile(r'/EmissorNacional/Notas/Download/NFSe/([^"\'&\s]{20,})')


def _autenticar_por_certificado(
    cert_path: str, cert_password: str, log: Callable[[str], None]
) -> requests.Session:
    """
    Autentica no portal via certificado (mTLS no host dedicado) e retorna uma
    Session com cookie de sessão válido em www.nfse.gov.br.
    Lança RuntimeError se a autenticação não for reconhecida.
    """
    cert_pem, key_pem = export_to_pem_files(cert_path, cert_password)
    session = requests.Session()
    session.cert = (cert_pem, key_pem)
    session.headers.update({"User-Agent": _UA, "Accept-Language": "pt-BR,pt;q=0.9"})

    # Login por certificado: o host dedicado exige o cert no TLS e redireciona
    # autenticado para o www (Dashboard), setando o cookie de sessão.
    session.get(CERT_LOGIN_URL, timeout=40, allow_redirects=True,
                headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})

    # Confirma que a sessão vale no www (não caiu na tela de login).
    r = session.get(f"{PORTAL}/EmissorNacional/", timeout=40, allow_redirects=True,
                    headers={"Accept": "text/html,*/*;q=0.8"})
    autenticado = ("EmissorNacional/Login" not in r.url
                   and 'name="Inscricao"' not in r.text
                   and 'placeholder="CPF/CNPJ"' not in r.text)
    if not autenticado:
        raise RuntimeError("Portal não reconheceu a autenticação por certificado.")
    log("  Reconciliação: autenticado no portal por certificado.")
    return session


def listar_chaves_recebidas_portal(
    cert_path: str,
    cert_password: str,
    data_inicial: date,
    data_final: date,
    log: Callable[[str], None] = print,
) -> Dict[str, str]:
    """
    Lista TODAS as NFS-e recebidas do período no portal.
    Retorna dict { chave44 : chave50 } (chave44 = 44 primeiros dígitos, usado para
    casar com o nome de arquivo truncado que a ferramenta grava).

    Paginação: parâmetro real do portal é 'pg' (o 'pagina' legado é ignorado).
    A listagem não passa por captcha.
    """
    session = _autenticar_por_certificado(cert_path, cert_password, log)
    di = data_inicial.strftime("%d/%m/%Y")
    df = data_final.strftime("%d/%m/%Y")

    chaves: Dict[str, str] = {}
    headers = {"Accept": "text/html,*/*;q=0.8", "Referer": f"{PORTAL}/EmissorNacional/"}

    pg = 1
    while pg <= MAX_PAGINAS:
        params = {"pg": pg, "busca": "", "datainicio": di, "datafim": df, "pagina": 1}
        try:
            r = session.get(RECEBIDAS_URL, params=params, headers=headers, timeout=40)
        except requests.RequestException as e:
            log(f"  Reconciliação: erro de rede na pág.{pg} ({e}); interrompendo listagem.")
            break

        # Sessão expirou no meio da varredura?
        if "EmissorNacional/Login" in r.url or 'name="Inscricao"' in r.text:
            raise RuntimeError("Sessão do portal expirou durante a reconciliação.")

        if r.status_code == 429:
            log("  Reconciliação: rate limit (429) no portal; aguardando 8s...")
            time.sleep(8)
            continue  # tenta a mesma página de novo
        if not r.ok:
            log(f"  Reconciliação: HTTP {r.status_code} na pág.{pg}; interrompendo.")
            break

        achadas = list(dict.fromkeys(_RE_CHAVE.findall(r.text)))
        novas = [c for c in achadas if c[:44] not in chaves]
        if not novas:
            break  # página sem chaves novas => fim da paginação
        for c in novas:
            chaves[c[:44]] = c
        pg += 1
        time.sleep(0.3)  # gentil com o portal

    log(f"  Reconciliação: {len(chaves)} nota(s) recebida(s) listada(s) no portal.")
    return chaves


def decode_prestador_da_chave(chave: str) -> tuple[str, str]:
    """
    Extrai (municipio_ibge, cnpj_prestador) da chave de acesso de 50 dígitos.
    Layout: [0:7]=código IBGE do município emissor, [9:23]=CNPJ do prestador.
    Retorna ("", "") se a chave não tiver o formato esperado.
    """
    ch = "".join(c for c in str(chave) if c.isdigit())
    if len(ch) < 23:
        return "", ""
    municipio = ch[:7]
    cnpj = ch[9:23]
    return municipio, cnpj


def reconciliar(
    chaves_portal: Dict[str, str],
    chaves_baixadas: set,
    enrich_nome: Callable[[str], str] | None = None,
) -> List[dict]:
    """
    Compara a listagem do portal com o que foi baixado (conjunto de chaves44).
    Retorna a lista de notas FALTANTES (no portal, ausentes no download), cada uma:
        { "chave", "municipio", "cnpj_prestador", "nome_prestador" }

    enrich_nome(cnpj_digits) -> nome (opcional, best-effort; use cnpj_lookup).
    """
    faltantes: List[dict] = []
    cache_nome: Dict[str, str] = {}
    for chave44, chave50 in chaves_portal.items():
        if chave44 in chaves_baixadas:
            continue
        municipio, cnpj = decode_prestador_da_chave(chave50)
        nome = ""
        if enrich_nome and cnpj:
            if cnpj not in cache_nome:
                try:
                    cache_nome[cnpj] = enrich_nome(cnpj) or ""
                except Exception:
                    cache_nome[cnpj] = ""
            nome = cache_nome[cnpj]
        faltantes.append({
            "chave": chave50,
            "municipio": municipio,
            "cnpj_prestador": cnpj,
            "nome_prestador": nome,
        })
    return faltantes

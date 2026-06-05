"""
Client for the Sistema Nacional NFS-e REST API.
Authentication: mutual TLS with A1 digital certificate.

Key endpoint: GET /contribuintes/DFe/{ultimoNSU}
- Returns up to 50 NFS-e documents per call (GZip+Base64 encoded XML)
- The CNPJ is identified automatically from the client certificate
- Iterate using the returned próxNSU until the list is empty
"""

import gzip
import base64
import json
import time
import xml.etree.ElementTree as ET
from datetime import date
from typing import List, Tuple, Callable

import requests

from app.services.cert_handler import export_to_pem_files

BASE_URL_PROD = "https://adn.nfse.gov.br/contribuintes"
BASE_URL_TEST = "https://adn.producaorestrita.nfse.gov.br/contribuintes"

MAX_ITERATIONS = 500   # safety cap (~25.000 notas)


class NfseNacionalClient:
    def __init__(
        self,
        cnpj: str,
        cert_path: str | None = None,
        cert_password: str | None = None,
        ambiente: str = "producao",
        session: "requests.Session | None" = None,
    ):
        """
        Parameters
        ----------
        cnpj          : CNPJ do contribuinte (apenas dígitos).
        cert_path     : Caminho para o arquivo .pfx/.p12 (auth por certificado).
        cert_password : Senha do certificado.
        ambiente      : "producao" ou "homologacao".
        session       : Sessão já autenticada (ex.: via Gov.br login).
                        Quando fornecida, cert_path/cert_password são ignorados.
        """
        self.cnpj = _only_digits(cnpj)
        self.cert_path = cert_path
        self.cert_password = cert_password
        self.base_url = BASE_URL_PROD if ambiente == "producao" else BASE_URL_TEST
        self._session: requests.Session | None = session  # may be pre-authenticated

    # ─── Session ──────────────────────────────────────────────────────────

    def _get_session(self) -> requests.Session:
        if self._session is None:
            if not self.cert_path:
                raise RuntimeError(
                    "Nenhum método de autenticação configurado. "
                    "Forneça cert_path ou uma session já autenticada."
                )
            cert_pem, key_pem = export_to_pem_files(self.cert_path, self.cert_password)
            session = requests.Session()
            session.cert = (cert_pem, key_pem)
            session.headers.update({"Accept": "application/json"})
            self._session = session
        return self._session

    # ─── DFe distribution (main download method) ──────────────────────────

    def consultar_por_periodo(
        self,
        data_inicial: date,
        data_final: date,
        log: Callable[[str], None] = print,
        nsu_inicial: int = 0,
    ) -> List[Tuple[str, str]]:
        """
        Download NFS-e for the given period.

        Phase 1 (when nsu_inicial==0): binary search to find the NSU closest
        to data_inicial — costs ~10 API calls instead of iterating everything.
        Phase 2: sequential download from that NSU until past data_final.
        No gaps — every NSU in the range is visited.
        """
        # ── Phase 1: find starting NSU ─────────────────────────────────────
        if nsu_inicial == 0:
            nsu = self._buscar_nsu_para_data(data_inicial, log)
        else:
            nsu = nsu_inicial

        log(f"  Baixando sequencialmente a partir do NSU {nsu} ...")

        # ── Phase 2: sequential download ───────────────────────────────────
        results: List[Tuple[str, str]] = []
        iteracoes = 0
        batches_apos_periodo = 0

        notas_apos_periodo = 0

        while iteracoes < MAX_ITERATIONS:
            iteracoes += 1
            batch, prox_nsu, fim = self._buscar_dfe_batch(nsu, log)

            if not batch:
                log(f"  Fim da fila (API sem mais documentos). Total: {len(results)} nota(s).")
                break

            datas_no_lote: list = []
            for chave, xml, dt_emissao in batch:
                if dt_emissao:
                    datas_no_lote.append(dt_emissao)
                if dt_emissao is not None and not (data_inicial <= dt_emissao <= data_final):
                    notas_apos_periodo += 1
                    continue
                results.append((chave, xml))
                log(f"  + NFS-e {chave}  [{dt_emissao or 'data?'}]")

            if datas_no_lote:
                log(f"  [NSU {nsu}→{prox_nsu-1}:  {min(datas_no_lote)} a {max(datas_no_lote)}  |  acumulado: {len(results)}]")

            # Só para quando a API sinaliza fim real — nunca por data.
            # Notas podem chegar fora de ordem no sistema nacional.
            if fim or prox_nsu == nsu:
                log(f"  Consulta concluída (fim da fila). Total: {len(results)} nota(s).")
                break

            nsu = prox_nsu
            time.sleep(5)

        log(f"  ({notas_apos_periodo} nota(s) fora do período ignorada(s))")
        return results

    def _buscar_nsu_para_data(self, data_alvo: date, log: Callable) -> int:
        """
        Find the NSU closest to data_alvo using exponential probing then binary search.
        Makes ~10-15 API calls. Returns an NSU slightly before data_alvo (with margin).
        """
        log(f"  Localizando NSU para {data_alvo} (busca binária) ...")

        def data_do_nsu(nsu: int) -> "date | None":
            batch, _, _ = self._buscar_dfe_batch_silencioso(nsu)
            if not batch:
                return None
            datas = [dt for _, _, dt in batch if dt]
            return max(datas) if datas else None

        # Step 1: exponential probe to find upper bound
        nsu_low, nsu_high = 0, 500
        while True:
            d = data_do_nsu(nsu_high)
            time.sleep(3)
            if d is None:
                # Reached end — data_alvo is beyond all docs, start from last known low
                break
            if d >= data_alvo:
                break
            nsu_low = nsu_high
            nsu_high = nsu_high * 2
            log(f"  Sondando NSU {nsu_high} ...")

        log(f"  Intervalo: NSU {nsu_low} a {nsu_high}. Refinando ...")

        # Step 2: binary search to within 200 NSUs
        while nsu_high - nsu_low > 200:
            mid = (nsu_low + nsu_high) // 2
            d = data_do_nsu(mid)
            time.sleep(3)
            if d is None or d >= data_alvo:
                nsu_high = mid
            else:
                nsu_low = mid

        # Return with a safety margin of 400 NSUs before the found point
        result = max(0, nsu_low - 400)
        log(f"  NSU encontrado: {result} (margem de 400 incluída)")
        return result

    def _buscar_dfe_batch_silencioso(self, nsu: int):
        """Same as _buscar_dfe_batch but without logging — used during binary search."""
        def _noop(_): pass
        return self._buscar_dfe_batch(nsu, _noop)

    def _buscar_dfe_batch(
        self, nsu: int, log: Callable[[str], None]
    ) -> Tuple[List[Tuple[str, str]], int, bool]:
        """
        Call GET /contribuintes/DFe/{nsu}.
        Returns (batch, prox_nsu, fim_da_fila).
        """
        url = f"{self.base_url}/DFe/{nsu}"
        retry = 0
        resp = None
        while retry < 3:
            try:
                resp = self._get_session().get(url, timeout=60)
            except requests.RequestException as e:
                log(f"  Erro de conexão: {e}")
                return [], nsu, True

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 0))
                wait = retry_after if retry_after > 0 else 10 * (retry + 1)
                log(f"  Rate limit (429) — aguardando {wait}s ...")
                time.sleep(wait)
                retry += 1
                continue
            break

        if resp is None or resp.status_code == 429:
            log("  Rate limit persistente. Tente novamente mais tarde.")
            return [], nsu, True

        if resp.status_code in (404, 204):
            return [], nsu, True

        if resp.status_code in (401, 403):
            log(f"  ⚠ Erro {resp.status_code} (não autorizado): {resp.text[:300]}")
            log("  ⚠ O token de autenticação foi rejeitado pela API de distribuição.")
            raise RuntimeError(
                f"Autenticação rejeitada pela API NFS-e (HTTP {resp.status_code}). "
                "O token de login/senha não é aceito pela API de distribuição de documentos. "
                "Use o Certificado Digital A1 para baixar NFS-e."
            )

        if not resp.ok:
            log(f"  Erro HTTP {resp.status_code}: {resp.text[:300]}")
            return [], nsu, True

        return self._parse_dfe_response(resp, log)

    def _parse_dfe_response(
        self, resp: requests.Response, log: Callable
    ) -> Tuple[List[Tuple[str, str, "date | None"]], int, bool]:
        """Parse the DFe response (JSON or XML) and extract NFS-e XMLs.
        Returns list of (chave, xml, data_emissao).
        """
        batch = []
        prox_nsu = 0
        fim = False

        content_type = resp.headers.get("Content-Type", "")

        # ── JSON response ──────────────────────────────────────────────────
        if "json" in content_type or resp.text.lstrip().startswith("{"):
            try:
                data = resp.json()

                docs = (
                    data.get("LoteDFe")
                    or data.get("docZip")
                    or data.get("loteDistDFeInt")
                    or []
                )
                if isinstance(docs, dict):
                    docs = [docs]

                for doc in docs:
                    tipo = (doc.get("TipoDocumento") or doc.get("tipoDocumento") or "NFSE").upper()
                    # Aceita NFS-e normais E canceladas. Descarta apenas EVENTOs puros.
                    if tipo not in ("NFSE", "NFS-E", "NFSE_CANCELADA", "NFS-E_CANCELADA", ""):
                        log(f"  [ignorado] NSU {doc.get('NSU')} tipo={tipo}")
                        continue

                    xml = _descompactar_doc(
                        doc.get("ArquivoXml")
                        or doc.get("docZip")
                        or doc.get("xml")
                        or ""
                    )
                    chave = (
                        doc.get("ChaveAcesso")
                        or doc.get("chaveAcesso")
                        or _extrair_chave(xml)
                        or str(doc.get("NSU") or "sem_chave")
                    )
                    dt = _extrair_data_emissao(xml)
                    if xml:
                        batch.append((chave, xml, dt))

                if docs:
                    ultimo_nsu = max(int(d.get("NSU") or d.get("nsu") or 0) for d in docs)
                    prox_nsu = ultimo_nsu + 1
                    # Log non-standard types found (for diagnostics)
                    tipos_extras = set(
                        (d.get("TipoDocumento") or "").upper() for d in docs
                        if (d.get("TipoDocumento") or "").upper() not in ("NFSE", "NFS-E", "EVENTO", "")
                    )
                    if tipos_extras:
                        log(f"  [tipos extras neste lote: {tipos_extras}]")
                else:
                    prox_nsu = 0

                status = data.get("StatusProcessamento") or data.get("status") or ""
                fim = not docs or status in ("SEM_DOCUMENTOS", "NAO_LOCALIZADO", "")

            except (json.JSONDecodeError, ValueError) as e:
                log(f"  Erro ao interpretar JSON: {e}")

        # ── XML response ───────────────────────────────────────────────────
        else:
            try:
                root = ET.fromstring(resp.text)
                prox_nsu_el = root.find(".//{*}proxNSU") or root.find(".//proxNSU")
                max_nsu_el = root.find(".//{*}maxNSU") or root.find(".//maxNSU")
                prox_nsu = int(prox_nsu_el.text) if prox_nsu_el is not None else 0
                max_nsu = int(max_nsu_el.text) if max_nsu_el is not None else 0
                fim = prox_nsu >= max_nsu if max_nsu else False

                for doc_el in list(root.iter("{*}docZip")) or list(root.iter("docZip")):
                    xml = _descompactar_doc(doc_el.text or "")
                    chave = _extrair_chave(xml) or "sem_chave"
                    dt = _extrair_data_emissao(xml)
                    if xml:
                        batch.append((chave, xml, dt))

            except ET.ParseError as e:
                log(f"  Erro ao interpretar XML: {e}")

        return batch, prox_nsu, fim

    # ─── Consulta por chave ────────────────────────────────────────────────

    def consultar_por_chave(self, chave_acesso: str) -> str:
        """GET /nfse/{chaveAcesso} — returns raw XML string."""
        resp = self._get_session().get(f"{self.base_url}/nfse/{chave_acesso}", timeout=30)
        resp.raise_for_status()
        return resp.text

    def baixar_por_chaves(
        self, chaves: List[str], log: Callable[[str], None] = print
    ) -> List[Tuple[str, str]]:
        """Download NFS-e XML for each access key. Returns (chave, xml) list."""
        results = []
        for chave in chaves:
            try:
                xml = self.consultar_por_chave(chave)
                results.append((chave, xml))
                log(f"  Baixado: {chave}")
            except Exception as e:
                log(f"  Erro em {chave}: {e}")
        return results


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _only_digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def _descompactar_doc(doc_zip: str) -> str:
    """Decode a GZip+Base64 encoded XML document."""
    if not doc_zip:
        return ""
    try:
        compressed = base64.b64decode(doc_zip)
        xml_bytes = gzip.decompress(compressed)
        return xml_bytes.decode("utf-8")
    except Exception:
        # Not GZip — might already be plain XML or base64-only
        try:
            return base64.b64decode(doc_zip).decode("utf-8")
        except Exception:
            return doc_zip  # return as-is (already plain XML)


def _extrair_chave(xml: str) -> str:
    """Try to extract chaveAcesso from an NFS-e XML."""
    if not xml:
        return ""
    try:
        root = ET.fromstring(xml)
        for tag in ("chaveAcesso", "ChaveAcesso", "chNFSe", "ChNFSe"):
            el = root.find(f".//{{{_ns(root)}}}{tag}") or root.find(f".//{tag}")
            if el is not None and el.text:
                return el.text.strip()
    except ET.ParseError:
        pass
    return ""


def _extrair_data_emissao(xml: str) -> "date | None":
    """
    Extract emission date from an NFS-e XML.
    Only looks at known emission date fields — never falls back to arbitrary elements
    to avoid picking up processing dates (dhProc) or other unrelated dates.
    Returns None if not found; caller must NOT filter out notes with None date.
    """
    if not xml:
        return None
    # Ordered by priority: emission date fields only
    EMISSION_TAGS = ("dhEmi", "DhEmi", "dEmi", "DataEmissao", "dataEmissao", "dhCompetencia")
    try:
        root = ET.fromstring(xml)
        ns = _ns(root)
        for tag in EMISSION_TAGS:
            # Try with document namespace
            el = root.find(f".//{{{ns}}}{tag}") if ns else None
            # Try without namespace
            if el is None:
                el = root.find(f".//{tag}")
            # Try with wildcard namespace search
            if el is None:
                el = root.find(f".//*[local-name()='{tag}']") if False else None  # ET doesn't support local-name
            if el is not None and el.text:
                text = el.text.strip()[:10]  # YYYY-MM-DD prefix
                try:
                    return date.fromisoformat(text)
                except ValueError:
                    pass
    except ET.ParseError:
        pass
    return None


def _ns(root) -> str:
    """Extract namespace from element tag."""
    if root.tag.startswith("{"):
        return root.tag[1:root.tag.index("}")]
    return ""



"""
Client for legacy ABRASF SOAP webservices (NFS-e municipal).
Used for municipalities not yet on the Sistema Nacional NFS-e.

The ABRASF standard uses signed XML + SOAP.
Authentication can be: mTLS, signed XML body, or username/password depending on the municipality.
"""

import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import List, Tuple, Callable

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from app.services.cert_handler import export_to_pem_files
    HAS_CERT = True
except ImportError:
    HAS_CERT = False

CONSULTAR_NFSE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<ConsultarNfseEnvio xmlns="http://www.abrasf.org.br/nfse.xsd">
  <Prestador>
    <CpfCnpj>
      <Cnpj>{cnpj}</Cnpj>
    </CpfCnpj>
    {inscricao_tag}
  </Prestador>
  <PeriodoEmissao>
    <DataInicial>{data_inicial}</DataInicial>
    <DataFinal>{data_final}</DataFinal>
  </PeriodoEmissao>
</ConsultarNfseEnvio>"""

SOAP_ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:e="http://www.abrasf.org.br/nfse.xsd">
  <soapenv:Header/>
  <soapenv:Body>
    <e:ConsultarNfseServico>
      <nfseCabecMsg><![CDATA[{cabec}]]></nfseCabecMsg>
      <nfseDadosMsg><![CDATA[{dados}]]></nfseDadosMsg>
    </e:ConsultarNfseServico>
  </soapenv:Body>
</soapenv:Envelope>"""

CABECALHO_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<cabecalho xmlns="http://www.abrasf.org.br/nfse.xsd" versao="2.02">
  <versaoDados>2.02</versaoDados>
</cabecalho>"""


class AbrasfClient:
    def __init__(
        self,
        cnpj: str,
        inscricao_municipal: str,
        webservice_url: str,
        cert_path: str = "",
        cert_password: str = "",
    ):
        self.cnpj = _only_digits(cnpj)
        self.inscricao_municipal = inscricao_municipal
        self.webservice_url = webservice_url
        self.cert_path = cert_path
        self.cert_password = cert_password

    def consultar_por_periodo(
        self,
        data_inicial: date,
        data_final: date,
        log: Callable[[str], None] = print,
    ) -> List[Tuple[str, str]]:
        """Query NFS-e from ABRASF webservice by period."""
        inscricao_tag = (
            f"<InscricaoMunicipal>{self.inscricao_municipal}</InscricaoMunicipal>"
            if self.inscricao_municipal
            else ""
        )

        dados = CONSULTAR_NFSE_TEMPLATE.format(
            cnpj=self.cnpj,
            inscricao_tag=inscricao_tag,
            data_inicial=data_inicial.strftime("%Y-%m-%d"),
            data_final=data_final.strftime("%Y-%m-%d"),
        )

        soap_body = SOAP_ENVELOPE.format(cabec=CABECALHO_TEMPLATE, dados=dados)

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "ConsultarNfse",
        }

        session = requests.Session()
        if self.cert_path and HAS_CERT:
            cert_pem, key_pem = export_to_pem_files(self.cert_path, self.cert_password)
            session.cert = (cert_pem, key_pem)

        log(f"Consultando ABRASF: {self.webservice_url}")
        resp = session.post(self.webservice_url, data=soap_body.encode("utf-8"), headers=headers, timeout=60)
        resp.raise_for_status()

        return self._parse_soap_response(resp.text, log)

    def _parse_soap_response(self, xml_text: str, log: Callable) -> List[Tuple[str, str]]:
        """Parse SOAP response and extract NFS-e XMLs."""
        results = []
        try:
            # Strip SOAP envelope
            root = ET.fromstring(xml_text)
            ns_map = {
                "soap": "http://schemas.xmlsoap.org/soap/envelope/",
                "n": "http://www.abrasf.org.br/nfse.xsd",
            }

            # Try to find CompNfse elements
            notas = (
                root.findall(".//n:CompNfse", ns_map)
                or root.findall(".//{http://www.abrasf.org.br/nfse.xsd}CompNfse")
                or root.findall(".//CompNfse")
            )

            for nota in notas:
                # Get NFS-e number as identifier
                numero_el = (
                    nota.find(".//{*}Numero")
                    or nota.find(".//Numero")
                )
                numero = numero_el.text if numero_el is not None else "sem_numero"
                xml_str = ET.tostring(nota, encoding="unicode")
                results.append((numero, xml_str))
                log(f"  NFS-e encontrada: número {numero}")

        except ET.ParseError as e:
            log(f"  Erro ao interpretar resposta SOAP: {e}")

        return results


def _only_digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())

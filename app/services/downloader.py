"""
Orchestrates downloading NFS-e XMLs for multiple clients and zipping results.
"""

import os
import zipfile
import threading
from datetime import date
from typing import List, Callable, Optional
from app.models.client import Client
from app.services.nfse_nacional import NfseNacionalClient
from app.services.abrasf_client import AbrasfClient


def _only_digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def _safe_filename(s: str) -> str:
    return "".join(c for c in s if c.isalnum() or c in "._- ").strip()


class DownloadJob:
    def __init__(
        self,
        clients: List[Client],
        data_inicial: date,
        data_final: date,
        output_dir: str,
        ambiente: str = "producao",
        nsu_inicial: int = 0,
        chaves_manuais: Optional[List[str]] = None,
        log_callback: Callable[[str], None] = print,
        progress_callback: Callable[[int, int], None] = lambda a, b: None,
        done_callback: Callable[[bool, str], None] = lambda ok, msg: None,
    ):
        self.clients = clients
        self.data_inicial = data_inicial
        self.data_final = data_final
        self.output_dir = output_dir
        self.ambiente = ambiente
        self.nsu_inicial = nsu_inicial
        self.chaves_manuais = chaves_manuais or []
        self.log = log_callback
        self.progress = progress_callback
        self.done = done_callback
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run_in_thread(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        return t

    def _run(self):
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            total = len(self.clients)
            for idx, client in enumerate(self.clients):
                if self._stop_event.is_set():
                    self.log("Download cancelado.")
                    self.done(False, "Cancelado pelo usuário.")
                    return

                self.log(f"\n[{idx+1}/{total}] Cliente: {client.nome} ({client.cnpj_formatado})")
                self.progress(idx, total)

                try:
                    xmls = self._download_client(client)
                    if not xmls:
                        self.log(f"  Nenhuma NFS-e encontrada para o período.")
                        continue

                    zip_path = self._zip_xmls(client, xmls)
                    self.log(f"  ZIP gerado: {zip_path} ({len(xmls)} nota(s))")

                except Exception as e:
                    self.log(f"  ERRO: {e}")

            self.progress(total, total)
            self.log(f"\nConcluído. Arquivos em: {self.output_dir}")
            self.done(True, f"Download concluído. Arquivos em:\n{self.output_dir}")

        except Exception as e:
            self.log(f"\nErro fatal: {e}")
            self.done(False, str(e))

    def _download_client(self, client: Client) -> List[tuple]:
        if client.api_tipo == "abrasf":
            api = AbrasfClient(
                cnpj=client.cnpj,
                inscricao_municipal=client.inscricao_municipal,
                webservice_url=client.abrasf_url,
                cert_path=client.cert_path,
                cert_password=client.cert_password,
            )
            if self.chaves_manuais:
                self.log("  ABRASF: consulta por chave manual não suportada; usando período.")
            return api.consultar_por_periodo(self.data_inicial, self.data_final, self.log)
        else:
            api = NfseNacionalClient(
                cnpj=client.cnpj,
                cert_path=client.cert_path,
                cert_password=client.cert_password,
                ambiente=self.ambiente,
            )
            if self.chaves_manuais:
                return api.baixar_por_chaves(self.chaves_manuais, self.log)
            return api.consultar_por_periodo(
                self.data_inicial, self.data_final, log=self.log, nsu_inicial=self.nsu_inicial
            )

    def _zip_xmls(self, client: Client, xmls: List[tuple]) -> str:
        cnpj_digits = _only_digits(client.cnpj)
        nome_safe = _safe_filename(client.nome)[:40]
        periodo = f"{self.data_inicial.strftime('%Y%m')}-{self.data_final.strftime('%Y%m')}"
        zip_name = f"NFS-e_{cnpj_digits}_{nome_safe}_{periodo}.zip"
        zip_path = os.path.join(self.output_dir, zip_name)

        seen_names: set = set()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, entry in enumerate(xmls, 1):
                identificador = entry[0]
                xml_content = entry[1]
                safe_id = _safe_filename(str(identificador))[:44] or "sem_chave"
                base_name = f"NFS-e_{safe_id}.xml"
                # Garante nome único mesmo com chaves duplicadas
                filename = base_name
                suffix = 1
                while filename in seen_names:
                    filename = f"NFS-e_{safe_id}_{suffix}.xml"
                    suffix += 1
                seen_names.add(filename)
                if isinstance(xml_content, str):
                    zf.writestr(filename, xml_content.encode("utf-8"))
                else:
                    zf.writestr(filename, xml_content)

        return zip_path

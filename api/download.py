"""
Vercel serverless function — NFS-e download endpoint.
Receives the A1 certificate in-memory, downloads NFS-e via the
Sistema Nacional NFS-e API and returns a ZIP file.
Certificate is never persisted — only used during the request.
"""

import sys
import os
import io
import json
import zipfile
import tempfile
import time
from datetime import date

from flask import Flask, request, send_file, jsonify

# Resolve project root so we can import app.services
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.cert_handler import export_to_pem_files
from app.services.nfse_nacional import NfseNacionalClient

app = Flask(__name__)


@app.route("/api/download", methods=["POST"])
def download():
    # ── 1. Validate inputs ────────────────────────────────────────────────
    cert_file = request.files.get("cert")
    if not cert_file:
        return jsonify({"error": "Arquivo de certificado (.pfx) não enviado."}), 400

    password    = request.form.get("password", "")
    cnpj        = request.form.get("cnpj", "").replace(".", "").replace("/", "").replace("-", "")
    nome        = request.form.get("nome", "Cliente")
    data_ini_s  = request.form.get("data_inicial", "")
    data_fim_s  = request.form.get("data_final", "")
    ambiente    = request.form.get("ambiente", "producao")
    nsu_inicial = int(request.form.get("nsu_inicial", 0) or 0)

    if not cnpj or len(cnpj) != 14:
        return jsonify({"error": "CNPJ inválido. Informe os 14 dígitos."}), 400

    try:
        data_ini = date.fromisoformat(data_ini_s)
        data_fim = date.fromisoformat(data_fim_s)
    except ValueError:
        return jsonify({"error": "Datas inválidas. Use AAAA-MM-DD."}), 400

    # ── 2. Save cert to temp file (in-memory processing) ─────────────────
    cert_bytes = cert_file.read()
    tmp_cert = tempfile.NamedTemporaryFile(delete=False, suffix=".pfx", prefix="nfse_upload_")
    tmp_cert.write(cert_bytes)
    tmp_cert.close()

    tmp_files = [tmp_cert.name]

    try:
        # ── 3. Validate certificate ───────────────────────────────────────
        try:
            from app.services.cert_handler import get_cert_info
            info = get_cert_info(tmp_cert.name, password)
            if info.get("vencido"):
                return jsonify({"error": f"Certificado vencido em {info['validade']}."}), 400
        except Exception as e:
            return jsonify({"error": f"Certificado inválido ou senha incorreta: {e}"}), 400

        # ── 4. Download NFS-e ─────────────────────────────────────────────
        messages = []
        def log(msg):
            messages.append(msg)

        client = NfseNacionalClient(
            cnpj=cnpj,
            cert_path=tmp_cert.name,
            cert_password=password,
            ambiente=ambiente,
        )

        results = client.consultar_por_periodo(
            data_inicial=data_ini,
            data_final=data_fim,
            log=log,
            nsu_inicial=nsu_inicial,
        )

        if not results:
            return jsonify({
                "error": "Nenhuma NFS-e encontrada para o período informado.",
                "log": messages,
            }), 404

        # ── 5. Build ZIP in memory ────────────────────────────────────────
        safe_nome = "".join(c for c in nome if c.isalnum() or c in " _-")[:40]
        periodo   = f"{data_ini.strftime('%Y%m')}-{data_fim.strftime('%Y%m')}"
        zip_name  = f"NFS-e_{cnpj}_{safe_nome}_{periodo}.zip"

        zip_buffer = io.BytesIO()
        seen_names: set = set()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry in results:
                chave = entry[0]
                xml   = entry[1]
                safe_chave = "".join(c for c in str(chave) if c.isalnum() or c in "._-")[:44]
                base  = f"NFS-e_{safe_chave}.xml"
                fname = base
                suf   = 1
                while fname in seen_names:
                    fname = f"NFS-e_{safe_chave}_{suf}.xml"
                    suf  += 1
                seen_names.add(fname)
                content = xml.encode("utf-8") if isinstance(xml, str) else xml
                zf.writestr(fname, content)

        zip_buffer.seek(0)

        # ── 6. Return ZIP ─────────────────────────────────────────────────
        return send_file(
            zip_buffer,
            mimetype="application/zip",
            as_attachment=True,
            download_name=zip_name,
        )

    finally:
        # Clean up temp files
        for f in tmp_files:
            try:
                os.unlink(f)
            except OSError:
                pass
        # Clean up PEM files created by cert_handler
        from app.services import cert_handler as _ch
        for f in list(_ch._temp_files):
            try:
                os.unlink(f)
                _ch._temp_files.remove(f)
            except (OSError, ValueError):
                pass

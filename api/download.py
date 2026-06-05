"""
Vercel serverless function — NFS-e download endpoint.
Returns a ZIP containing all NFS-e XMLs + an XLSX summary.
Certificate is never persisted — only used during the request lifecycle.
"""

import sys, os, io, json, zipfile, tempfile
import xml.etree.ElementTree as ET
from datetime import date

from flask import Flask, request, send_file, jsonify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.cert_handler import export_to_pem_files, get_cert_info
from app.services.nfse_nacional import NfseNacionalClient
from app.services import cert_handler as _ch

app = Flask(__name__)


# ── NFS-e XML parser ─────────────────────────────────────────────────────────

NS = "http://www.sped.fazenda.gov.br/nfse"

def _txt(root, *tags) -> str:
    """Find first matching tag (with or without namespace) and return its text."""
    for tag in tags:
        for candidate in (f"{{{NS}}}{tag}", tag):
            el = root.find(f".//{candidate}")
            if el is not None and el.text:
                return el.text.strip()
    return ""


def parse_nfse(chave: str, nsu: int, xml: str) -> dict:
    row = {
        "ChaveAcesso": chave,
        "NSU": nsu,
        "NumeroNFSe": "",
        "DataEmissao": "",
        "Competencia": "",
        "CNPJPrestador": "",
        "NomePrestador": "",
        "CNPJTomador": "",
        "NomeTomador": "",
        "DescricaoServico": "",
        "ValorServico": "",
        "ValorISSQN": "",
        "Aliquota": "",
        "MunicipioIncidencia": "",
    }
    try:
        root = ET.fromstring(xml)
        row["NumeroNFSe"]          = _txt(root, "nNFSe")
        row["DataEmissao"]         = _txt(root, "dhEmi", "dEmi", "DataEmissao")[:10]
        row["Competencia"]         = _txt(root, "dComp", "competencia", "Competencia")[:10]
        row["CNPJPrestador"]       = _txt(root, "emit//CNPJ", "prestador//CNPJ", "CNPJ")
        row["NomePrestador"]       = _txt(root, "emit//xNome", "prestador//xNome", "xNome")
        row["CNPJTomador"]         = _txt(root, "tomador//CNPJ")
        row["NomeTomador"]         = _txt(root, "tomador//xNome")
        row["DescricaoServico"]    = _txt(root, "xDescServ", "descricao")
        row["ValorServico"]        = _txt(root, "vServ", "vServPrest//vReceb", "ValorServicos")
        row["ValorISSQN"]          = _txt(root, "vISSQN", "vIss", "ValorIss")
        row["Aliquota"]            = _txt(root, "pAliq", "aliquota", "Aliquota")
        row["MunicipioIncidencia"] = _txt(root, "cLocIncid", "cMunFG", "CodigoMunicipio")

        # Fallback: CNPJ do prestador pode estar no topo do XML (emit ou infDPS/emit)
        if not row["CNPJPrestador"]:
            row["CNPJPrestador"] = _txt(root, "CNPJ")
    except ET.ParseError:
        pass
    return row


# ── XLSX builder ─────────────────────────────────────────────────────────────

COLUNAS = [
    ("ChaveAcesso",        "Chave de Acesso",          50),
    ("NSU",                "NSU",                       8),
    ("NumeroNFSe",         "Número NFS-e",             14),
    ("DataEmissao",        "Data Emissão",             14),
    ("Competencia",        "Competência",              14),
    ("CNPJPrestador",      "CNPJ Prestador",           20),
    ("NomePrestador",      "Nome Prestador",           40),
    ("CNPJTomador",        "CNPJ Tomador",             20),
    ("NomeTomador",        "Nome Tomador",             40),
    ("DescricaoServico",   "Descrição Serviço",        40),
    ("ValorServico",       "Valor Serviço (R$)",       18),
    ("ValorISSQN",         "Valor ISSQN (R$)",         18),
    ("Aliquota",           "Alíquota (%)",             14),
    ("MunicipioIncidencia","Município Incidência",     20),
]


def build_xlsx(rows: list) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "NFS-e"

    header_font  = Font(bold=True, color="FFFFFF", size=11)
    header_fill  = PatternFill("solid", fgColor="1F6AA5")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border  = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"),  bottom=Side(style="thin"),
    )
    alt_fill = PatternFill("solid", fgColor="EBF2FB")

    # Header
    for col, (_, label, width) in enumerate(COLUNAS, 1):
        cell = ws.cell(row=1, column=col, value=label)
        cell.font   = header_font
        cell.fill   = header_fill
        cell.alignment = header_align
        cell.border = thin_border
        ws.column_dimensions[cell.column_letter].width = width

    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

    # Data rows
    for r, row in enumerate(rows, 2):
        fill = alt_fill if r % 2 == 0 else None
        for col, (key, _, _) in enumerate(COLUNAS, 1):
            val = row.get(key, "")
            cell = ws.cell(row=r, column=col, value=val)
            cell.border = thin_border
            cell.alignment = Alignment(vertical="center")
            if fill:
                cell.fill = fill

    # Auto-filter
    ws.auto_filter.ref = ws.dimensions

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# ── Main endpoint ─────────────────────────────────────────────────────────────

@app.route("/api/download", methods=["POST"])
def download():
    cert_file = request.files.get("cert")
    if not cert_file:
        return jsonify({"error": "Arquivo de certificado (.pfx) não enviado."}), 400

    password   = request.form.get("password", "")
    cnpj       = "".join(c for c in request.form.get("cnpj", "") if c.isdigit())
    nome       = request.form.get("nome", "Cliente")
    data_ini_s = request.form.get("data_inicial", "")
    data_fim_s = request.form.get("data_final", "")
    ambiente   = request.form.get("ambiente", "producao")
    nsu_ini    = int(request.form.get("nsu_inicial", 0) or 0)

    if len(cnpj) != 14:
        return jsonify({"error": "CNPJ inválido. Informe os 14 dígitos."}), 400

    try:
        data_ini = date.fromisoformat(data_ini_s)
        data_fim = date.fromisoformat(data_fim_s)
    except ValueError:
        return jsonify({"error": "Datas inválidas. Use AAAA-MM-DD."}), 400

    # Save cert to temp file
    tmp_cert = tempfile.NamedTemporaryFile(delete=False, suffix=".pfx", prefix="nfse_up_")
    tmp_cert.write(cert_file.read())
    tmp_cert.close()

    try:
        # Validate cert
        try:
            info = get_cert_info(tmp_cert.name, password)
            if info.get("vencido"):
                return jsonify({"error": f"Certificado vencido em {info['validade']}."}), 400
        except Exception as e:
            return jsonify({"error": f"Certificado inválido ou senha incorreta: {e}"}), 400

        # Download NFS-e
        messages = []
        client = NfseNacionalClient(
            cnpj=cnpj, cert_path=tmp_cert.name,
            cert_password=password, ambiente=ambiente,
        )
        results = client.consultar_por_periodo(
            data_inicial=data_ini, data_final=data_fim,
            log=messages.append, nsu_inicial=nsu_ini,
        )

        if not results:
            return jsonify({
                "error": "Nenhuma NFS-e encontrada para o período informado.",
                "log": messages,
            }), 404

        # Build XLSX rows
        xlsx_rows = []
        for i, entry in enumerate(results):
            chave = entry[0]
            xml   = entry[1]
            nsu_val = i + 1  # fallback; ideally pass NSU from the batch
            xlsx_rows.append(parse_nfse(chave, nsu_val, xml))

        # Build ZIP (XMLs + XLSX)
        safe_nome = "".join(c for c in nome if c.isalnum() or c in " _-")[:40]
        periodo   = f"{data_ini.strftime('%Y%m')}-{data_fim.strftime('%Y%m')}"
        zip_name  = f"NFS-e_{cnpj}_{safe_nome}_{periodo}.zip"

        zip_buf = io.BytesIO()
        seen: set = set()

        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # XML files
            for i, entry in enumerate(results, 1):
                chave = entry[0]
                xml   = entry[1]
                safe_chave = "".join(c for c in str(chave) if c.isalnum())[:44]
                base  = f"xml/NFS-e_{safe_chave}.xml"
                fname = base
                suf   = 1
                while fname in seen:
                    fname = f"xml/NFS-e_{safe_chave}_{suf}.xml"
                    suf  += 1
                seen.add(fname)
                content = xml.encode("utf-8") if isinstance(xml, str) else xml
                zf.writestr(fname, content)

            # XLSX
            xlsx_bytes = build_xlsx(xlsx_rows)
            zf.writestr(f"NFS-e_{cnpj}_{periodo}.xlsx", xlsx_bytes)

        zip_buf.seek(0)
        return send_file(zip_buf, mimetype="application/zip",
                         as_attachment=True, download_name=zip_name)

    finally:
        try:
            os.unlink(tmp_cert.name)
        except OSError:
            pass
        for f in list(_ch._temp_files):
            try:
                os.unlink(f)
                _ch._temp_files.remove(f)
            except (OSError, ValueError):
                pass

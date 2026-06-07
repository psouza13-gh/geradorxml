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
from app.services.auth_service import verify_token
from app.services.subscription_service import verificar_e_registrar_download

app = Flask(__name__)

MAX_CERT_BYTES = 1 * 1024 * 1024  # 1 MB


# ── NFS-e XML parser ─────────────────────────────────────────────────────────

NS = "http://www.sped.fazenda.gov.br/nfse"

_digits = lambda s: "".join(c for c in s if c.isdigit())


def _txt(root, *tags) -> str:
    """Find first matching tag (with or without namespace) and return its text."""
    for tag in tags:
        for candidate in (f"{{{NS}}}{tag}", tag):
            el = root.find(f".//{candidate}")
            if el is not None and el.text:
                return el.text.strip()
    return ""


def parse_nfse(chave: str, nsu: int, xml: str, cnpj_contribuinte: str = "") -> dict:
    row = {
        "ChaveAcesso": chave,
        "NSU": nsu,
        "Tipo": "Indefinida",   # Emitida / Recebida / Indefinida
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
        row["CNPJPrestador"]       = _txt(root, "emit//CNPJ", "prestador//CNPJ",
                                          "prest//CNPJ", "Prestador//CNPJ")
        row["NomePrestador"]       = _txt(root, "emit//xNome", "prestador//xNome",
                                          "prest//xNome", "xNome")
        row["CNPJTomador"]         = _txt(root, "tomador//CNPJ", "dest//CNPJ",
                                          "Tomador//CNPJ")
        row["NomeTomador"]         = _txt(root, "tomador//xNome", "dest//xNome")
        row["DescricaoServico"]    = _txt(root, "xDescServ", "descricao", "Descricao")
        row["ValorServico"]        = _txt(root, "vServ", "vServPrest//vReceb", "ValorServicos")
        row["ValorISSQN"]          = _txt(root, "vISSQN", "vIss", "ValorIss")
        row["Aliquota"]            = _txt(root, "pAliq", "aliquota", "Aliquota")
        row["MunicipioIncidencia"] = _txt(root, "cLocIncid", "cMunFG", "CodigoMunicipio")

        # Fallback: CNPJ do prestador pode estar no topo do XML
        if not row["CNPJPrestador"]:
            row["CNPJPrestador"] = _txt(root, "CNPJ")
    except ET.ParseError:
        pass

    # ── Classify: Emitida / Recebida / Indefinida ─────────────────────────
    # Rule: based solely on CNPJPrestador vs the client's CNPJ.
    #   Emitida  → client IS the prestador (they issued the invoice)
    #   Recebida → another CNPJ is the prestador (issued BY someone else TO/for client)
    #   Indefinida → can't extract prestador CNPJ from the XML
    if cnpj_contribuinte:
        cnpj_d  = _digits(cnpj_contribuinte)
        prest_d = _digits(row["CNPJPrestador"])
        tom_d   = _digits(row["CNPJTomador"])

        if prest_d == cnpj_d:
            row["Tipo"] = "Emitida"          # client emitted this note
        elif prest_d:
            row["Tipo"] = "Recebida"         # another prestador emitted it
        elif tom_d == cnpj_d:
            row["Tipo"] = "Recebida"         # client is tomador → received
        # else: prestador unknown, tomador unknown → stays "Indefinida"

    return row


def _filter_by_tipo(results: list, xlsx_rows: list, tipo: str) -> tuple:
    """
    Filter results and xlsx_rows by NFS-e type:
    • "todas"    → keep everything
    • "emitidas" → only Emitida  (strict: client must be prestador)
    • "recebidas"→ Recebida + Indefinida (conservative: keep unknowns to avoid losses)
    """
    if tipo == "todas":
        return results, xlsx_rows

    keep = {
        "emitidas":  {"Emitida"},
        "recebidas": {"Recebida", "Indefinida"},
    }.get(tipo, {"Emitida", "Recebida", "Indefinida"})

    paired = [(r, row) for r, row in zip(results, xlsx_rows) if row["Tipo"] in keep]
    if not paired:
        return [], []
    res, rows = zip(*paired)
    return list(res), list(rows)


# ── XLSX builder ─────────────────────────────────────────────────────────────

COLUNAS = [
    ("ChaveAcesso",        "Chave de Acesso",          50),
    ("NSU",                "NSU",                       8),
    ("Tipo",               "Tipo",                     12),  # Emitida / Recebida / Indefinida
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

    # Fills for "Tipo" column
    tipo_fills = {
        "Emitida":   PatternFill("solid", fgColor="D4EDDA"),  # light green
        "Recebida":  PatternFill("solid", fgColor="D1ECF1"),  # light blue
        "Indefinida":PatternFill("solid", fgColor="FFF3CD"),  # light yellow
    }

    # Data rows
    for r, row in enumerate(rows, 2):
        alt = r % 2 == 0
        for col, (key, _, _) in enumerate(COLUNAS, 1):
            val = row.get(key, "")
            cell = ws.cell(row=r, column=col, value=val)
            cell.border = thin_border
            cell.alignment = Alignment(vertical="center")
            if key == "Tipo":
                # Always apply tipo color regardless of alternating row
                cell.fill = tipo_fills.get(val, alt_fill if alt else PatternFill())
                cell.font = Font(bold=True, size=10)
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif alt:
                cell.fill = alt_fill

    # Auto-filter
    ws.auto_filter.ref = ws.dimensions

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# ── Main endpoint ─────────────────────────────────────────────────────────────

@app.route("/api/download", methods=["POST"])
def download():
    # ── Common fields ──────────────────────────────────────────────────────────
    auth_method = request.form.get("auth_method", "cert")   # "cert" | "login" (login temporariamente desativado)
    if auth_method == "login":
        return jsonify({"error": "O acesso por login e senha do portal está temporariamente desativado. "
                                  "Por enquanto, utilize o certificado digital A1 (.pfx/.p12)."}), 400
    cnpj        = "".join(c for c in request.form.get("cnpj", "") if c.isdigit())
    nome        = request.form.get("nome", "Cliente")
    data_ini_s  = request.form.get("data_inicial", "")
    data_fim_s  = request.form.get("data_final", "")
    ambiente    = request.form.get("ambiente", "producao")
    nsu_ini     = int(request.form.get("nsu_inicial", 0) or 0)
    tipo_nfse   = request.form.get("tipo_nfse", "todas")
    if tipo_nfse not in ("todas", "emitidas", "recebidas"):
        tipo_nfse = "todas"

    if len(cnpj) != 14:
        return jsonify({"error": "CNPJ inválido. Informe os 14 dígitos."}), 400

    # ── Auth check ──────────────────────────────────────────────────────────────
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Autenticação necessária."}), 401
    jwt_payload = verify_token(auth_header[7:])
    if not jwt_payload:
        return jsonify({"error": "Token inválido ou expirado. Faça login novamente."}), 401

    user_id = jwt_payload["sub"]

    # ── Subscription / trial check ──────────────────────────────────────────────
    allowed, msg = verificar_e_registrar_download(user_id, cnpj)
    if not allowed:
        return jsonify({"error": msg}), 403

    try:
        data_ini = date.fromisoformat(data_ini_s)
        data_fim = date.fromisoformat(data_fim_s)
    except ValueError:
        return jsonify({"error": "Datas inválidas. Use AAAA-MM-DD."}), 400

    tmp_cert_path = None
    messages = []

    try:
        # ── Authentication (Certificado Digital A1 — único método disponível) ──
        cert_file = request.files.get("cert")
        if not cert_file:
            return jsonify({"error": "Arquivo de certificado (.pfx) não enviado."}), 400

        password = request.form.get("password", "")

        # ── File size guard ─────────────────────────────────────────────
        cert_bytes = cert_file.read()
        if len(cert_bytes) > MAX_CERT_BYTES:
            return jsonify({"error": "Certificado muito grande (máx. 1 MB)."}), 400

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pfx", prefix="nfse_up_")
        tmp.write(cert_bytes)
        tmp.close()
        tmp_cert_path = tmp.name

        try:
            info = get_cert_info(tmp_cert_path, password)
            if info.get("vencido"):
                return jsonify({"error": f"Certificado vencido em {info['validade']}."}), 400
        except Exception:
            return jsonify({"error": "Certificado inválido ou senha incorreta."}), 400

        client = NfseNacionalClient(
            cnpj=cnpj, cert_path=tmp_cert_path,
            cert_password=password, ambiente=ambiente,
        )

        # ── Download NFS-e ─────────────────────────────────────────────────────
        try:
            results = client.consultar_por_periodo(
                data_inicial=data_ini, data_final=data_fim,
                log=messages.append, nsu_inicial=nsu_ini,
                tipo_scraping=tipo_nfse,
            )
        except RuntimeError as e:
            return jsonify({"error": str(e), "log": messages}), 400

        if not results:
            return jsonify({
                "error": "Nenhuma NFS-e encontrada para o período informado.",
                "log": messages,
            }), 404

        # ── Parse + classify every note ────────────────────────────────────
        xlsx_rows = []
        for i, entry in enumerate(results):
            chave   = entry[0]
            xml     = entry[1]
            nsu_val = i + 1
            xlsx_rows.append(parse_nfse(chave, nsu_val, xml, cnpj_contribuinte=cnpj))

        # ── Apply tipo filter (after full download — never miss a note) ────
        total_baixadas = len(results)
        results, xlsx_rows = _filter_by_tipo(results, xlsx_rows, tipo_nfse)

        tipo_label = {"emitidas": "emitidas", "recebidas": "recebidas"}.get(tipo_nfse, "")
        if not results:
            tipo_msg = f" {tipo_label}" if tipo_label else ""
            return jsonify({
                "error": (
                    f"Nenhuma NFS-e{tipo_msg} encontrada no período informado "
                    f"(total baixado: {total_baixadas}, nenhuma classificada como {tipo_label or 'qualquer tipo'})."
                ),
                "log": messages,
            }), 404

        # ── Summary counts by type ──────────────────────────────────────────
        contagem = {"Emitida": 0, "Recebida": 0, "Indefinida": 0}
        for row in xlsx_rows:
            contagem[row["Tipo"]] = contagem.get(row["Tipo"], 0) + 1
        messages.append(
            f"  Resumo: {len(xlsx_rows)} nota(s) exportadas "
            f"[Emitidas: {contagem['Emitida']} | Recebidas: {contagem['Recebida']} | Indefinidas: {contagem['Indefinida']}]"
            + (f" — filtro: {tipo_label}" if tipo_label else "")
        )

        # ── Build ZIP (XMLs + XLSX) ─────────────────────────────────────────
        safe_nome   = "".join(c for c in nome if c.isalnum() or c in " _-")[:40]
        periodo     = f"{data_ini.strftime('%Y%m')}-{data_fim.strftime('%Y%m')}"
        tipo_suffix = {"emitidas": "_Emitidas", "recebidas": "_Recebidas"}.get(tipo_nfse, "")
        zip_name    = f"NFS-e_{cnpj}_{safe_nome}_{periodo}{tipo_suffix}.zip"

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
            zf.writestr(f"NFS-e_{cnpj}_{periodo}{tipo_suffix}.xlsx", xlsx_bytes)

        zip_buf.seek(0)
        return send_file(zip_buf, mimetype="application/zip",
                         as_attachment=True, download_name=zip_name)

    finally:
        if tmp_cert_path:
            try:
                os.unlink(tmp_cert_path)
            except OSError:
                pass
        for f in list(_ch._temp_files):
            try:
                os.unlink(f)
                _ch._temp_files.remove(f)
            except (OSError, ValueError):
                pass

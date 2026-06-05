"""
Handles loading A1 digital certificates (.pfx / .p12) and exporting
temporary PEM files for use with the requests library (mTLS).
"""

import os
import tempfile
import atexit
from typing import Tuple
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption
from cryptography import x509

_temp_files: list[str] = []


def _cleanup_temps():
    for path in _temp_files:
        try:
            os.unlink(path)
        except OSError:
            pass


atexit.register(_cleanup_temps)


def load_pfx(pfx_path: str, password: str) -> Tuple[object, object]:
    """Return (private_key, certificate) from a .pfx file."""
    with open(pfx_path, "rb") as f:
        pfx_data = f.read()
    pw = password.encode("utf-8") if isinstance(password, str) else password
    private_key, certificate, _ = pkcs12.load_key_and_certificates(pfx_data, pw)
    return private_key, certificate


def export_to_pem_files(pfx_path: str, password: str) -> Tuple[str, str]:
    """
    Load a .pfx certificate and export it to temporary PEM files.
    Returns (cert_pem_path, key_pem_path) — files are auto-deleted on exit.
    """
    private_key, certificate = load_pfx(pfx_path, password)

    key_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    cert_pem = certificate.public_bytes(Encoding.PEM)

    key_file = tempfile.NamedTemporaryFile(delete=False, suffix=".key.pem", prefix="nfse_")
    cert_file = tempfile.NamedTemporaryFile(delete=False, suffix=".cert.pem", prefix="nfse_")

    key_file.write(key_pem)
    cert_file.write(cert_pem)
    key_file.close()
    cert_file.close()

    _temp_files.extend([key_file.name, cert_file.name])
    return cert_file.name, key_file.name


def get_cert_info(pfx_path: str, password: str) -> dict:
    """Return basic info about the certificate for display."""
    _, certificate = load_pfx(pfx_path, password)
    subject = certificate.subject
    not_after = certificate.not_valid_after_utc

    def get_attr(oid):
        try:
            return subject.get_attributes_for_oid(oid)[0].value
        except (IndexError, Exception):
            return ""

    return {
        "cn": get_attr(x509.NameOID.COMMON_NAME),
        "cnpj": get_attr(x509.NameOID.SERIAL_NUMBER),
        "validade": not_after.strftime("%d/%m/%Y"),
        "vencido": not_after < __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    }

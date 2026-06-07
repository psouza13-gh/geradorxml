"""
At-rest encryption helpers for sensitive personal data (CPF, telefone, ...).

Design:
  - Values are encrypted with Fernet (AES-128-CBC + HMAC, authenticated)
    using a key from the DATA_ENCRYPTION_KEY environment variable.
  - Encryption is non-deterministic (random IV per call), so it CANNOT be
    used directly for uniqueness lookups in SQL (`WHERE col = ...`).
  - For fields that need a uniqueness/dedup check (e.g. CPF — one trial
    per person), we additionally store a deterministic SHA-256 hash
    (`*_hash` columns) salted with a server-side pepper. The hash never
    reveals the plaintext and is only used for equality comparisons.

Generate a key once with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
and set it as DATA_ENCRYPTION_KEY in the Vercel project environment
(Production + Preview) — never commit it to the repo.
"""
import os
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

_ENC_KEY = os.environ.get("DATA_ENCRYPTION_KEY", "")
# A secondary, independent secret used to salt one-way hashes. Falls back to
# JWT_SECRET so the app keeps working even before DATA_HASH_PEPPER is set,
# but configuring a dedicated value is recommended.
_HASH_PEPPER = os.environ.get("DATA_HASH_PEPPER") or os.environ.get("JWT_SECRET", "change-me-before-production")


@lru_cache(maxsize=1)
def _fernet() -> Fernet | None:
    if not _ENC_KEY:
        return None
    try:
        return Fernet(_ENC_KEY.encode("utf-8"))
    except Exception:
        return None


def encrypt(value: str | None) -> str | None:
    """Encrypt a string for storage. Returns None if value is falsy."""
    if not value:
        return None
    f = _fernet()
    if f is None:
        raise RuntimeError("DATA_ENCRYPTION_KEY environment variable not set or invalid.")
    return f.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt(token: str | None) -> str | None:
    """Decrypt a value previously produced by encrypt(). Returns None on failure."""
    if not token:
        return None
    f = _fernet()
    if f is None:
        return None
    try:
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        return None


def deterministic_hash(value: str | None) -> str | None:
    """
    One-way, salted hash for equality/uniqueness lookups (e.g. "has this CPF
    already registered a trial?"). Always normalize the value (strip
    non-digits, etc.) BEFORE calling this so equivalent inputs hash equal.
    """
    if not value:
        return None
    digest = hashlib.sha256(f"{_HASH_PEPPER}:{value}".encode("utf-8")).hexdigest()
    return digest

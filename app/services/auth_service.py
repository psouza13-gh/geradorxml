"""
Authentication helpers: JWT creation/verification + bcrypt password hashing.
"""
import os
import uuid
import secrets
import hashlib
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt

from app.services.db import execute
from app.services.crypto_service import encrypt, decrypt, deterministic_hash
from app.services.validators import normalize_cpf, normalize_telefone

JWT_SECRET      = os.environ.get("JWT_SECRET", "change-me-before-production")
JWT_ALGO        = "HS256"
JWT_EXPIRY_HOURS = 72


# ── Password ──────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def verify_token(token: str) -> dict | None:
    """Decode and verify a JWT. Returns the payload dict or None on failure."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ── User CRUD ─────────────────────────────────────────────────────────────────

def cpf_already_registered(cpf: str) -> bool:
    """
    True if a normalized CPF has already been used to create an account.
    Used to block mass trial creation (one trial per person/CPF).
    Lookup is done via the deterministic hash — the plaintext CPF is never
    used in a WHERE clause, so it's never exposed via timing/SQL surfaces.
    """
    cpf_hash = deterministic_hash(normalize_cpf(cpf))
    if not cpf_hash:
        return False
    row = execute(
        "SELECT 1 FROM users WHERE cpf_hash = %s LIMIT 1",
        (cpf_hash,),
        fetch="one",
    )
    return row is not None


def create_user(nome: str, email: str, password: str,
                cpf: str | None = None, telefone: str | None = None) -> dict | None:
    """
    Create a new user with a 3-day trial plan (2 CNPJs). Returns the user dict.

    CPF and telefone are personal data (LGPD-sensitive): they are stored
    ENCRYPTED at rest (Fernet, app/services/crypto_service.py). CPF is also
    stored as a one-way salted hash so we can detect duplicate signups
    (anti mass-account-creation) without ever comparing/storing plaintext
    in a searchable column.
    """
    user_id       = str(uuid.uuid4())
    now           = datetime.now(timezone.utc)
    trial_expires = now + timedelta(days=3)
    pw_hash       = hash_password(password)

    cpf_norm  = normalize_cpf(cpf)
    tel_norm  = normalize_telefone(telefone)
    cpf_hash  = deterministic_hash(cpf_norm) if cpf_norm else None
    cpf_enc   = encrypt(cpf_norm) if cpf_norm else None
    tel_enc   = encrypt(tel_norm) if tel_norm else None

    execute(
        """
        INSERT INTO users
            (id, nome, email, password_hash, plano, cnpj_limite,
             status, trial_expires_at, created_at,
             cpf_hash, cpf_encrypted, telefone_encrypted)
        VALUES (%s, %s, %s, %s, 'trial', 2, 'ativo', %s, %s, %s, %s, %s)
        """,
        (user_id, nome, email.lower(), pw_hash, trial_expires, now,
         cpf_hash, cpf_enc, tel_enc),
    )
    return get_user_by_id(user_id)


def get_user_by_email(email: str) -> dict | None:
    row = execute(
        "SELECT * FROM users WHERE email = %s",
        (email.lower(),),
        fetch="one",
    )
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> dict | None:
    row = execute(
        """
        SELECT id, nome, email, password_hash,
               plano, cnpj_limite, status,
               trial_expires_at, trial_locked_cnpj,
               asaas_customer_id, is_admin, vitalicio,
               acesso_expires_at, plano_origem, created_at,
               cpf_encrypted, telefone_encrypted
        FROM users WHERE id = %s
        """,
        (user_id,),
        fetch="one",
    )
    if not row:
        return None
    user = dict(row)
    # Decrypt for the owner's own view (never expose cpf_hash; raw encrypted
    # blobs are decrypted on-demand only, never persisted in plaintext).
    user["cpf"]      = decrypt(user.pop("cpf_encrypted", None))
    user["telefone"] = decrypt(user.pop("telefone_encrypted", None))
    return user


def update_password(user_id: str, new_password: str) -> None:
    execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (hash_password(new_password), user_id),
    )


# ── Password reset (emailed code) ─────────────────────────────────────────────

RESET_CODE_TTL_MINUTES = 15
RESET_CODE_COOLDOWN_SECONDS = 60   # min. time between two codes for the same user


def _hash_code(code: str) -> str:
    # Plain SHA-256 is fine here: codes are short-lived, single-use, numeric,
    # and rate-limited — unlike passwords, there's no long-term secret to protect
    # against offline brute force, and bcrypt would be needless overhead per request.
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def create_password_reset_code(user_id: str) -> str | None:
    """
    Generate a 6-digit single-use code, store its hash with a 15-minute
    expiry, and return the plaintext code for the caller to email out.
    Returns None if a code was already issued too recently (cooldown) —
    callers should treat that as a soft-success ("check your email") to
    avoid revealing account existence/timing to an attacker.
    """
    now = datetime.now(timezone.utc)
    recent = execute(
        """
        SELECT created_at FROM password_resets
         WHERE user_id = %s AND used_at IS NULL
         ORDER BY created_at DESC LIMIT 1
        """,
        (user_id,),
        fetch="one",
    )
    if recent and (now - recent["created_at"]).total_seconds() < RESET_CODE_COOLDOWN_SECONDS:
        return None

    code = f"{secrets.randbelow(1_000_000):06d}"
    execute(
        """
        INSERT INTO password_resets (user_id, code_hash, expires_at, created_at)
        VALUES (%s, %s, %s, %s)
        """,
        (user_id, _hash_code(code), now + timedelta(minutes=RESET_CODE_TTL_MINUTES), now),
    )
    return code


def verify_and_consume_reset_code(user_id: str, code: str) -> bool:
    """
    Validate a reset code (matches hash, not expired, not used) and mark it
    used atomically. Returns True iff the code was valid and just consumed.
    """
    if not code or not code.isdigit():
        return False
    now = datetime.now(timezone.utc)
    row = execute(
        """
        UPDATE password_resets
           SET used_at = %s
         WHERE id = (
                SELECT id FROM password_resets
                 WHERE user_id = %s AND code_hash = %s
                   AND used_at IS NULL AND expires_at > %s
                 ORDER BY created_at DESC LIMIT 1
             )
        RETURNING id
        """,
        (now, user_id, _hash_code(code), now),
        fetch="one",
    )
    return row is not None

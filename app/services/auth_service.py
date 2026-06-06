"""
Authentication helpers: JWT creation/verification + bcrypt password hashing.
"""
import os
import uuid
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt

from app.services.db import execute

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

def create_user(nome: str, email: str, password: str) -> dict | None:
    """Create a new user with a 24h trial plan. Returns the user dict."""
    user_id       = str(uuid.uuid4())
    now           = datetime.now(timezone.utc)
    trial_expires = now + timedelta(hours=24)
    pw_hash       = hash_password(password)

    execute(
        """
        INSERT INTO users
            (id, nome, email, password_hash, plano, cnpj_limite,
             status, trial_expires_at, created_at)
        VALUES (%s, %s, %s, %s, 'trial', 1, 'ativo', %s, %s)
        """,
        (user_id, nome, email.lower(), pw_hash, trial_expires, now),
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
               asaas_customer_id, created_at
        FROM users WHERE id = %s
        """,
        (user_id,),
        fetch="one",
    )
    return dict(row) if row else None

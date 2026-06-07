-- ═══════════════════════════════════════════════════════════════
--  Migration: CPF/telefone capture (anti mass-account-creation)
--             + password reset via emailed code
--
--  - cpf_hash:           one-way salted SHA-256 of the normalized CPF.
--                        Indexed + unique → enforces "one trial per CPF"
--                        WITHOUT ever storing/searching plaintext CPF.
--  - cpf_encrypted:      Fernet-encrypted CPF (app-level, DATA_ENCRYPTION_KEY).
--                        Reversible only by the app, for the user's own view
--                        / support purposes. Never used in WHERE clauses.
--  - telefone_encrypted: Fernet-encrypted phone number, same rationale.
--
--  Safe to run multiple times (IF NOT EXISTS guards).
--  Run once on your Neon project: psql $DATABASE_URL -f migration_cpf_phone_reset.sql
-- ═══════════════════════════════════════════════════════════════

ALTER TABLE users ADD COLUMN IF NOT EXISTS cpf_hash           TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS cpf_encrypted      TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS telefone_encrypted TEXT;

-- Enforce "one trial account per CPF" (existing rows have NULL cpf_hash,
-- so they're naturally exempt — the partial index only covers non-null values).
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_cpf_hash
    ON users (cpf_hash) WHERE cpf_hash IS NOT NULL;

-- Password reset codes (short-lived, single-use, emailed to the account owner)
CREATE TABLE IF NOT EXISTS password_resets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash   TEXT NOT NULL,             -- SHA-256(code) — the plaintext code is never stored
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_password_resets_user  ON password_resets (user_id);
CREATE INDEX IF NOT EXISTS idx_password_resets_code  ON password_resets (code_hash);

-- ═══════════════════════════════════════════════════════════════
--  geradorxml — Neon Postgres schema
--  Run once on your Neon project: psql $DATABASE_URL -f schema.sql
-- ═══════════════════════════════════════════════════════════════

-- Users / accounts
CREATE TABLE IF NOT EXISTS users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nome                TEXT NOT NULL,
    email               TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,

    -- Subscription state
    plano               TEXT NOT NULL DEFAULT 'trial',   -- trial | starter | pro | office | bpo
    cnpj_limite         INT  NOT NULL DEFAULT 1,         -- max CNPJs/month (-1 = unlimited)
    status              TEXT NOT NULL DEFAULT 'ativo',   -- ativo | suspenso | cancelado | congelado

    -- Trial
    trial_expires_at    TIMESTAMPTZ,
    trial_locked_cnpj   TEXT,                            -- CNPJ locked on first download

    -- ASAAS
    asaas_customer_id   TEXT,

    -- Super admin / manual subscription management
    is_admin            BOOLEAN NOT NULL DEFAULT FALSE,
    vitalicio           BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE = lifetime access (no billing/expiry)
    acesso_expires_at   TIMESTAMPTZ,                     -- expiry of an admin-granted temporary access
    plano_origem        TEXT NOT NULL DEFAULT 'trial',   -- trial | asaas | admin_temporario | admin_vitalicio
    status_anterior     TEXT,                            -- status saved before freezing (to restore on unfreeze)

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_asaas ON users (asaas_customer_id);

-- Saved clients (per user)
CREATE TABLE IF NOT EXISTS clients (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    nome             TEXT NOT NULL,
    cnpj             TEXT NOT NULL,
    municipio_codigo TEXT,
    municipio_nome   TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, cnpj)
);

CREATE INDEX IF NOT EXISTS idx_clients_user ON clients (user_id);

-- Monthly download usage (for plan enforcement)
CREATE TABLE IF NOT EXISTS monthly_usage (
    id                 BIGSERIAL PRIMARY KEY,
    user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cnpj               TEXT NOT NULL,
    mes                TEXT NOT NULL,          -- 'YYYY-MM'
    download_count     INT  NOT NULL DEFAULT 1,
    first_download_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, cnpj, mes)
);

CREATE INDEX IF NOT EXISTS idx_usage_user_mes ON monthly_usage (user_id, mes);

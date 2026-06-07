-- ═══════════════════════════════════════════════════════════════
--  Migration: Super Admin panel — manual subscription management
--  Adds columns needed for: admin flag, lifetime/temporary grants,
--  freeze/unfreeze, and tracking where a plan came from.
--
--  Safe to run multiple times (IF NOT EXISTS guards).
--  Run once on your Neon project: psql $DATABASE_URL -f migration_admin_panel.sql
-- ═══════════════════════════════════════════════════════════════

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin          BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS vitalicio         BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS acesso_expires_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS plano_origem      TEXT NOT NULL DEFAULT 'trial';
ALTER TABLE users ADD COLUMN IF NOT EXISTS status_anterior   TEXT;

-- Backfill plano_origem for existing rows so reporting/MRR makes sense:
--   anyone already on a paid plan with an asaas_customer_id came from ASAAS;
--   everyone else stays as 'trial' (the column default).
UPDATE users
   SET plano_origem = 'asaas'
 WHERE plano_origem = 'trial'
   AND asaas_customer_id IS NOT NULL
   AND plano <> 'trial';

CREATE INDEX IF NOT EXISTS idx_users_is_admin ON users (is_admin) WHERE is_admin = TRUE;

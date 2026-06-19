-- ═══════════════════════════════════════════════════════════════
--  geradorxml — Stripe migration
--  Run once: psql $DATABASE_URL -f migration_stripe.sql
-- ═══════════════════════════════════════════════════════════════

-- Add Stripe customer + subscription IDs (parallel to asaas_customer_id)
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS stripe_customer_id     TEXT,
    ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;

CREATE INDEX IF NOT EXISTS idx_users_stripe_customer
    ON users (stripe_customer_id)
    WHERE stripe_customer_id IS NOT NULL;

-- Add 'stripe' as a valid plano_origem (no enum constraint in this schema,
-- but documenting the new value for clarity)
-- plano_origem values: trial | asaas | admin_temporario | admin_vitalicio | stripe

-- Optional: mark existing admin-granted subscribers clearly
-- (no data change needed — just schema additions above)

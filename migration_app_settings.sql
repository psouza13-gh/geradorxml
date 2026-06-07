-- ═══════════════════════════════════════════════════════════════
--  Migration: app_settings (generic key/value config store)
--  Used initially for the Meta Conversions API integration panel.
--  Run once: psql $DATABASE_URL -f migration_app_settings.sql
--  Idempotent — safe to run multiple times.
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed the Meta Conversions API config row (admin-editable via /admin panel).
-- NOTE: the access token is intentionally NOT stored here — it lives only in
-- the META_CAPI_ACCESS_TOKEN environment variable (server-side secret).
INSERT INTO app_settings (key, value)
VALUES ('meta_capi', '{
    "enabled": false,
    "pixel_id": "",
    "test_event_code": "",
    "events": { "lead": true, "trial": true, "purchase": true }
}'::jsonb)
ON CONFLICT (key) DO NOTHING;

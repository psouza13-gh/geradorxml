-- ═══════════════════════════════════════════════════════════════
--  geradorxml — Controle de e-mails de reengajamento
--  Opcional: o app cria esta tabela sozinho (CREATE TABLE IF NOT EXISTS).
--    psql "$DATABASE_URL" -f migration_engagement.sql
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS engagement_messages (
    id       BIGSERIAL PRIMARY KEY,
    user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tipo     TEXT NOT NULL,            -- welcome | reminder | winback
    sent_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, tipo)
);

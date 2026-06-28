-- ═══════════════════════════════════════════════════════════════
--  geradorxml — Rate limiting (tentativas por janela de tempo)
--  Opcional: o app cria esta tabela sozinho (CREATE TABLE IF NOT EXISTS)
--  na primeira requisição. Rode para já deixá-la pronta:
--    psql "$DATABASE_URL" -f migration_rate_limit.sql
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS rate_limit_hits (
    id         BIGSERIAL PRIMARY KEY,
    bucket     TEXT NOT NULL,          -- ex.: 'login:1.2.3.4'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rl_bucket_time ON rate_limit_hits (bucket, created_at);

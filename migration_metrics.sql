-- ═══════════════════════════════════════════════════════════════
--  geradorxml — Migração para Métricas de SaaS & Logs Operacionais
-- ═══════════════════════════════════════════════════════════════

-- 1. Coluna para rastreabilidade de churn mensal
ALTER TABLE users ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;

-- 2. Tabela para logs operacionais de downloads (Error Rate)
CREATE TABLE IF NOT EXISTS download_logs (
    id          SERIAL PRIMARY KEY,
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    cnpj        TEXT,
    sucesso     BOOLEAN NOT NULL,
    erro        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexar para otimizar pesquisas de período dos últimos 30 dias
CREATE INDEX IF NOT EXISTS idx_download_logs_created_at ON download_logs (created_at);

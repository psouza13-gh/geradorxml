-- ═══════════════════════════════════════════════════════════
--  Seed: admin user (BPO plan — unlimited access)
--  Run once against your Neon database:
--    psql $DATABASE_URL -f seed_admin.sql
-- ═══════════════════════════════════════════════════════════

INSERT INTO users (
    id,
    nome,
    email,
    password_hash,
    plano,
    cnpj_limite,
    status,
    trial_expires_at,
    trial_locked_cnpj,
    created_at
)
VALUES (
    'da438d3b-19cb-4664-8f8c-d04bb7464b04',
    'Admin',
    'juliasouza2203@gmail.com',
    '$2b$12$MOZIZ.U7XSE5km2AghcKJOszklz3OW76Fs2EN8h..c1TQDLYP02o.',
    'bpo',
    -1,
    'ativo',
    NULL,
    NULL,
    NOW()
)
ON CONFLICT (email) DO UPDATE
    SET plano        = 'bpo',
        cnpj_limite  = -1,
        status       = 'ativo',
        trial_expires_at   = NULL,
        trial_locked_cnpj  = NULL;

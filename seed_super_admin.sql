-- ═══════════════════════════════════════════════════════════════
--  Seed: SUPER ADMIN account (owner — full access + admin panel)
--
--  email: p.esouzaf@gmail.com
--  senha: (definida no momento da criação — hash bcrypt abaixo)
--
--  Run once against your Neon database (after migration_admin_panel.sql):
--    psql $DATABASE_URL -f seed_super_admin.sql
-- ═══════════════════════════════════════════════════════════════

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
    is_admin,
    vitalicio,
    acesso_expires_at,
    plano_origem,
    created_at
)
VALUES (
    gen_random_uuid(),
    'Pedro Souza',
    'p.esouzaf@gmail.com',
    '$2b$12$vAAKVLracLlTQfc1SalMVepNEAvRAni74KjcBNkwpnyDp5hVCbBge',
    'bpo',
    -1,
    'ativo',
    NULL,
    NULL,
    TRUE,
    TRUE,
    NULL,
    'admin_vitalicio',
    NOW()
)
ON CONFLICT (email) DO UPDATE
    SET password_hash = EXCLUDED.password_hash,
        plano             = 'bpo',
        cnpj_limite       = -1,
        status            = 'ativo',
        trial_expires_at  = NULL,
        trial_locked_cnpj = NULL,
        is_admin          = TRUE,
        vitalicio         = TRUE,
        acesso_expires_at = NULL,
        plano_origem      = 'admin_vitalicio';

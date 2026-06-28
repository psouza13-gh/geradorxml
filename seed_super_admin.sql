-- ═══════════════════════════════════════════════════════════════
--  Seed: SUPER ADMIN account (owner — full access + admin panel)
--
--  email: p.esouzaf@gmail.com
--  senha: o hash bcrypt NÃO fica versionado. Gere e passe na hora.
--
--  1) Gere o hash localmente (NUNCA commitar):
--       python -c "import bcrypt;print(bcrypt.hashpw(b'SUA_SENHA',bcrypt.gensalt()).decode())"
--  2) Rode passando o hash como variável psql:
--       psql "$DATABASE_URL" -v admin_pw_hash="'<HASH_GERADO>'" -f seed_super_admin.sql
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
    :admin_pw_hash,
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

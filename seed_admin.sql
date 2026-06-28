-- ═══════════════════════════════════════════════════════════
--  Seed: admin user (BPO plan — unlimited access)
--  O hash bcrypt NÃO fica versionado. Gere e passe na hora:
--    python -c "import bcrypt;print(bcrypt.hashpw(b'SUA_SENHA',bcrypt.gensalt()).decode())"
--    psql "$DATABASE_URL" -v admin_pw_hash="'<HASH_GERADO>'" -f seed_admin.sql
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
    :admin_pw_hash,
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

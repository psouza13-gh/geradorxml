-- ═══════════════════════════════════════════════════════════════
--  geradorxml — Backfill de clientes a partir do histórico de uso
--
--  OPCIONAL (não bloqueia nada) — reduz atrito para usuários ativos.
--
--  Com a nova regra de "cadastro de cliente obrigatório antes de baixar",
--  o formulário de download só lista CNPJs já cadastrados na aba Clientes.
--  Usuários que baixaram NFS-e ANTES dessa mudança podem ter usado um CNPJ
--  sem nunca ter criado um "cliente" formal para ele — o backend já permite
--  que continuem baixando esse CNPJ (grandfather clause), mas ele não
--  aparecerá no dropdown até virar um registro em `clients`.
--
--  Este script cria esse registro automaticamente (nome = "Cliente {CNPJ}")
--  para todo (usuário, CNPJ) que já tem histórico em monthly_usage e ainda
--  não tem uma linha correspondente em clients. Idempotente — seguro rodar
--  mais de uma vez.
--
--    psql "$DATABASE_URL" -f migration_backfill_clients.sql
-- ═══════════════════════════════════════════════════════════════

INSERT INTO clients (id, user_id, nome, cnpj, municipio_codigo, municipio_nome, created_at)
SELECT gen_random_uuid(), mu.user_id, 'Cliente ' || mu.cnpj, mu.cnpj, '', '', NOW()
  FROM (SELECT DISTINCT user_id, cnpj FROM monthly_usage) mu
  LEFT JOIN clients c ON c.user_id = mu.user_id AND c.cnpj = mu.cnpj
 WHERE c.id IS NULL;

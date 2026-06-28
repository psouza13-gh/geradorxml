"""
Rate limiting simples, backed by Postgres (serverless não tem memória
compartilhada entre invocações, então um contador em processo não funciona).

Uso:
    from app.services.rate_limit import limited
    if limited(f"login:{ip}", limit=10, window=300):
        return jsonify({"error": "Muitas tentativas. Tente novamente em alguns minutos."}), 429

Filosofia: o limitador FALHA ABERTO (em caso de erro de DB ele permite),
porque indisponibilidade do limitador não deve derrubar o login dos usuários.
Isso é seguro: falhar aberto aqui só significa "sem throttle momentâneo",
nunca um bypass de autenticação.
"""
from app.services.db import execute

_ensured = False


def _ensure() -> None:
    global _ensured
    if _ensured:
        return
    execute(
        """
        CREATE TABLE IF NOT EXISTS rate_limit_hits (
            id         BIGSERIAL PRIMARY KEY,
            bucket     TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    execute(
        "CREATE INDEX IF NOT EXISTS idx_rl_bucket_time ON rate_limit_hits (bucket, created_at)"
    )
    # Limpeza oportunista: remove hits com mais de 1 dia (mantém a tabela enxuta).
    try:
        execute("DELETE FROM rate_limit_hits WHERE created_at < NOW() - INTERVAL '1 day'")
    except Exception:
        pass
    _ensured = True


def limited(bucket: str, limit: int, window: int) -> bool:
    """
    Return True if *bucket* JÁ atingiu o limite na janela (deve ser bloqueado).
    Quando ainda permitido, registra o hit e retorna False.

    bucket : chave lógica, ex. "login:1.2.3.4"
    limit  : número máximo de tentativas na janela
    window : tamanho da janela em segundos
    """
    try:
        _ensure()
        row = execute(
            "SELECT COUNT(*) AS n FROM rate_limit_hits "
            "WHERE bucket = %s AND created_at > NOW() - (%s || ' seconds')::interval",
            (bucket, window),
            fetch="one",
        )
        n = row["n"] if row else 0
        if n >= limit:
            return True
        execute("INSERT INTO rate_limit_hits (bucket) VALUES (%s)", (bucket,))
        return False
    except Exception:
        # Fail open — nunca bloquear usuário legítimo por erro do limitador.
        return False

"""
/api/cron/engagement — sequência de reengajamento por e-mail (Vercel Cron)

Envia 3 e-mails (welcome / reminder / winback) para usuários do plano gratuito
que se cadastraram mas NUNCA fizeram um download de sucesso (download_logs).
A sequência para sozinha assim que a pessoa ativa (faz o 1º download).

Segurança: exige Authorization: Bearer <CRON_SECRET> — a Vercel injeta esse
header automaticamente nas chamadas de cron quando a env CRON_SECRET existe.

Uso manual / verificação:
  curl -H "Authorization: Bearer <CRON_SECRET>" \
       "https://geradorxml.hubfiscal.app.br/api/cron/engagement?dry=1"   # só lista, não envia
"""
import sys, os, hmac
from flask import Flask, request, jsonify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.db import execute
from app.services.email_service import (
    send_engagement_welcome,
    send_engagement_reminder,
    send_engagement_winback,
)

app = Flask(__name__)

# (tipo, dia_min, dia_max, função de envio)
# janela = cadastrado entre dia_max e dia_min dias atrás.
_TIERS = [
    ("welcome",  1, 2, send_engagement_welcome),
    ("reminder", 3, 5, send_engagement_reminder),
    ("winback",  7, 10, send_engagement_winback),
]

_MAX_POR_NIVEL = 200


def _ensure_table() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS engagement_messages (
            id       BIGSERIAL PRIMARY KEY,
            user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tipo     TEXT NOT NULL,
            sent_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, tipo)
        )
        """
    )


def _authorized() -> bool:
    secret = os.environ.get("CRON_SECRET", "").strip()
    if not secret:
        return False
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {secret}"
    return hmac.compare_digest(auth, expected)


def _cohort(tipo: str, dia_min: int, dia_max: int):
    return execute(
        """
        SELECT u.id, u.nome, u.email
          FROM users u
         WHERE u.plano = 'trial' AND u.status = 'ativo' AND u.is_admin = FALSE
           AND u.created_at <= NOW() - (%s * INTERVAL '1 day')
           AND u.created_at >  NOW() - (%s * INTERVAL '1 day')
           AND NOT EXISTS (SELECT 1 FROM download_logs d
                            WHERE d.user_id = u.id AND d.sucesso = TRUE)
           AND NOT EXISTS (SELECT 1 FROM engagement_messages e
                            WHERE e.user_id = u.id AND e.tipo = %s)
         ORDER BY u.created_at ASC
         LIMIT %s
        """,
        (dia_min, dia_max, tipo, _MAX_POR_NIVEL),
        fetch="all",
    )


_FNS = {
    "welcome":  send_engagement_welcome,
    "reminder": send_engagement_reminder,
    "winback":  send_engagement_winback,
}


@app.route("/api/cron/engagement", methods=["GET", "POST"])
def engagement():
    if not _authorized():
        return jsonify({"error": "Unauthorized"}), 401

    # ── Modo de teste: envia o(s) e-mail(s) na hora para um endereço, ────────
    # ignorando a janela/cohort. Não grava em engagement_messages (é só teste).
    #   ?test=all|welcome|reminder|winback&to=email[&nome=Pedro]
    test = (request.args.get("test") or "").lower().strip()
    if test:
        to = (request.args.get("to") or "").strip()
        if not to:
            return jsonify({"error": "Informe ?to=email"}), 400
        nome = request.args.get("nome") or "Pedro"
        alvos = ["welcome", "reminder", "winback"] if test == "all" else [test]
        if any(t not in _FNS for t in alvos):
            return jsonify({"error": "test deve ser welcome | reminder | winback | all"}), 400
        resultado = {}
        for t in alvos:
            try:
                resultado[t] = "enviado" if _FNS[t](to, nome) else "falhou"
            except Exception as exc:
                resultado[t] = f"erro: {exc}"
        return jsonify({"ok": True, "test": True, "to": to, "resultado": resultado})

    dry = (request.args.get("dry") or "").lower() in ("1", "true", "yes")

    try:
        _ensure_table()
    except Exception as exc:
        return jsonify({"error": f"Falha ao preparar tabela: {exc}"}), 500

    report = {}
    for tipo, dia_min, dia_max, enviar in _TIERS:
        rows = _cohort(tipo, dia_min, dia_max) or []

        if dry:
            report[tipo] = {
                "receberiam": len(rows),
                "amostra": [r["email"] for r in rows[:50]],
            }
            continue

        enviados, falhas = 0, 0
        for r in rows:
            ok = False
            try:
                ok = enviar(r["email"], r["nome"])
            except Exception:
                ok = False
            if ok:
                try:
                    execute(
                        "INSERT INTO engagement_messages (user_id, tipo) VALUES (%s, %s) "
                        "ON CONFLICT (user_id, tipo) DO NOTHING",
                        (r["id"], tipo),
                    )
                    enviados += 1
                except Exception:
                    pass
            else:
                falhas += 1
        report[tipo] = {"enviados": enviados, "falhas": falhas}

    return jsonify({"ok": True, "dry": dry, "report": report})

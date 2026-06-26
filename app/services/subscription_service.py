"""
Subscription / plan enforcement logic.

Free plan rules (plano='trial' kept as the internal id):
  - Free forever, NO time expiry
  - 5 CNPJ slots total / lifetime (counted by distinct CNPJs ever downloaded)
  - First CNPJ also recorded in trial_locked_cnpj (back-compat with the
    client-edit lock and the UI "locked" badge)

Paid plan limits (CNPJs per month):
  starter  →  15
  pro      →  50
  office   → 150
  bpo      →  -1  (unlimited)
"""
from datetime import datetime, timezone

from app.services.db import execute


# Number of distinct CNPJs allowed on the free plan (lifetime, no time limit).
FREE_CNPJ_LIMIT = 5

PLANO_LIMITES: dict[str, int] = {
    "trial":   FREE_CNPJ_LIMIT,
    "starter": 15,
    "pro":     50,
    "office":  150,
    "bpo":     -1,
}


def get_cnpj_limite(plano: str) -> int:
    """Return the monthly CNPJ limit for a plan (-1 = unlimited)."""
    return PLANO_LIMITES.get((plano or "").lower(), 1)


def verificar_e_registrar_download(user_id: str, cnpj: str) -> tuple[bool, str]:
    """
    Check whether *user_id* is allowed to download for *cnpj*.
    On success, record the download in monthly_usage and (for trial) lock the CNPJ.

    Returns:
        (True,  "")            — allowed
        (False, "<message>")   — not allowed; message shown to the user
    """
    user = execute(
        """
        SELECT plano, status, trial_expires_at, trial_locked_cnpj,
               vitalicio, acesso_expires_at, plano_origem
          FROM users WHERE id = %s
        """,
        (user_id,),
        fetch="one",
    )

    if not user:
        return False, "Usuário não encontrado."

    status = user["status"]
    plano  = (user["plano"] or "trial").lower()

    if status == "cancelado":
        return False, "Sua conta foi cancelada. Reative sua assinatura para continuar."

    if status == "suspenso":
        return False, "Sua conta está suspensa por falta de pagamento. Atualize o pagamento para continuar."

    if status == "congelado":
        return False, "Sua conta está temporariamente congelada pelo administrador. Entre em contato com o suporte."

    now = datetime.now(timezone.utc)

    # ── Admin-granted temporary access: auto-expire ───────────────────────
    if not user.get("vitalicio") and (user.get("plano_origem") == "admin_temporario"):
        acesso_expires = user.get("acesso_expires_at")
        if acesso_expires:
            if acesso_expires.tzinfo is None:
                acesso_expires = acesso_expires.replace(tzinfo=timezone.utc)
            if now > acesso_expires:
                execute(
                    "UPDATE users SET status = 'suspenso' WHERE id = %s",
                    (user_id,),
                )
                return (
                    False,
                    "Seu acesso temporário concedido pelo administrador expirou. "
                    "Entre em contato com o suporte para renovar.",
                )

    if plano == "trial":
        # ── Free plan: forever, no time expiry ─────────────────────────────
        # Limited to FREE_CNPJ_LIMIT distinct CNPJs total (lifetime).
        # Count distinct CNPJs ever used by this user (all months).
        cnpjs_usados = execute(
            "SELECT DISTINCT cnpj FROM monthly_usage WHERE user_id = %s",
            (user_id,),
            fetch="all",
        )
        cnpjs_set = {r["cnpj"] for r in (cnpjs_usados or [])}
        # Back-compat: a legacy account may have a locked CNPJ recorded before
        # its first monthly_usage row existed.
        locked_cnpj = user.get("trial_locked_cnpj")
        if locked_cnpj:
            cnpjs_set.add(locked_cnpj)

        if cnpj not in cnpjs_set and len(cnpjs_set) >= FREE_CNPJ_LIMIT:
            return (
                False,
                f"Você já usou seus {FREE_CNPJ_LIMIT} CNPJs gratuitos. "
                f"Assine um plano para adicionar mais CNPJs.",
            )

        # Keep trial_locked_cnpj pointing to the FIRST CNPJ used (back-compat
        # with the client edit/delete lock and the "locked" badge in the UI).
        if not locked_cnpj:
            execute(
                "UPDATE users SET trial_locked_cnpj = %s WHERE id = %s",
                (cnpj, user_id),
            )

    else:
        # ── Paid plan: monthly CNPJ count ──────────────────────────────────
        limite = get_cnpj_limite(plano)
        if limite != -1:
            mes = now.strftime("%Y-%m")
            cnpjs_usados = execute(
                "SELECT DISTINCT cnpj FROM monthly_usage WHERE user_id = %s AND mes = %s",
                (user_id, mes),
                fetch="all",
            )
            cnpjs_set = {r["cnpj"] for r in (cnpjs_usados or [])}

            if cnpj not in cnpjs_set and len(cnpjs_set) >= limite:
                return (
                    False,
                    f"Limite de {limite} CNPJs/mês atingido no plano "
                    f"{plano.capitalize()}. Faça upgrade para continuar.",
                )

    # ── Register this download ─────────────────────────────────────────────
    mes = now.strftime("%Y-%m")
    execute(
        """
        INSERT INTO monthly_usage (user_id, cnpj, mes, download_count, first_download_at)
        VALUES (%s, %s, %s, 1, %s)
        ON CONFLICT (user_id, cnpj, mes)
        DO UPDATE SET download_count = monthly_usage.download_count + 1
        """,
        (user_id, cnpj, mes, now),
    )

    return True, ""


def cliente_bloqueado_por_trial(user_id: str, client_id: str) -> bool:
    """
    Return True if this client is the trial-locked CNPJ and the user is on trial.
    Locked clients cannot be edited or deleted.
    """
    client = execute(
        "SELECT cnpj FROM clients WHERE id = %s AND user_id = %s",
        (client_id, user_id),
        fetch="one",
    )
    if not client:
        return False

    user = execute(
        "SELECT plano, trial_locked_cnpj FROM users WHERE id = %s",
        (user_id,),
        fetch="one",
    )
    if not user:
        return False

    return (
        (user["plano"] or "").lower() == "trial"
        and user["trial_locked_cnpj"] == client["cnpj"]
    )


def get_uso_mensal(user_id: str) -> list[dict]:
    """Return this month's download records for the user."""
    now = datetime.now(timezone.utc)
    mes = now.strftime("%Y-%m")

    rows = execute(
        """
        SELECT cnpj, download_count, first_download_at
        FROM monthly_usage
        WHERE user_id = %s AND mes = %s
        ORDER BY first_download_at ASC
        """,
        (user_id, mes),
        fetch="all",
    )

    result = []
    for r in (rows or []):
        d = dict(r)
        if d.get("first_download_at"):
            d["first_download_at"] = d["first_download_at"].isoformat()
        result.append(d)
    return result

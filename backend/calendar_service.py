"""
Google Calendar integration — cria eventos automaticamente ao encerrar sessões.
Fire-and-forget: nunca levanta exceção para não quebrar o fluxo principal.
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL    = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
TIMEZONE_BR         = "America/Sao_Paulo"


async def _obter_access_token(refresh_token: str) -> str:
    """Troca o refresh_token por um access_token válido."""
    import google_auth as ga
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "client_id":     ga.GOOGLE_CLIENT_ID,
            "client_secret": ga.GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type":    "refresh_token",
        })
    resp.raise_for_status()
    return resp.json()["access_token"]


async def criar_evento_sessao(
    owner_email: str,
    paciente_nome: str,
    data_sessao: str,
    resumo_notas: str | None = None,
) -> str | None:
    """
    Cria um evento no Google Calendar primário do fisio.
    - owner_email: e-mail do fisio (dono da sessão)
    - paciente_nome: nome do paciente
    - data_sessao: string ISO da data/hora da sessão (ex: '2026-04-06T10:30:00')
    - resumo_notas: texto resumido da sessão para a descrição do evento
    Retorna o event_id do Google Calendar, ou None em caso de falha.
    """
    import database as db

    refresh_token = db.get_google_refresh_token(owner_email)
    if not refresh_token:
        logger.debug("criar_evento_sessao: %s não tem refresh_token — pulando", owner_email)
        return None

    try:
        access_token = await _obter_access_token(refresh_token)
    except Exception as exc:
        logger.warning("criar_evento_sessao: falha ao obter access_token para %s: %s", owner_email, exc)
        return None

    # Monta data/hora de início (usa a data da sessão, horário 09:00 como fallback)
    try:
        dt_base = datetime.fromisoformat(data_sessao.replace("Z", "+00:00"))
    except Exception:
        dt_base = datetime.now(timezone.utc)

    # Mantém a data, usa hora do registro se tiver, senão 09:00
    start = dt_base.replace(tzinfo=dt_base.tzinfo or timezone.utc)
    end   = start + timedelta(hours=1)

    descricao = resumo_notas or "Sessão registrada via Physio Notes."

    event_body = {
        "summary":     f"Physio — {paciente_nome}",
        "description": descricao,
        "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE_BR},
        "end":   {"dateTime": end.isoformat(),   "timeZone": TIMEZONE_BR},
        "colorId": "2",  # verde — cor de "Sage" no Google Calendar
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{GOOGLE_CALENDAR_API}/calendars/primary/events",
                json=event_body,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
        if resp.status_code in (200, 201):
            event_id = resp.json().get("id")
            logger.info("criar_evento_sessao: evento criado id=%s para %s", event_id, owner_email)
            return event_id
        else:
            logger.warning("criar_evento_sessao: Calendar API retornou %s: %s", resp.status_code, resp.text[:200])
            return None
    except Exception as exc:
        logger.warning("criar_evento_sessao: exceção ao criar evento: %s", exc)
        return None

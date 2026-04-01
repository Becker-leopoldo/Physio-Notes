import asyncio
import logging
import httpx
from services.base import BaseService

logger = logging.getLogger(__name__)

INFOSIMPLES_URL = "https://api.infosimples.com/api/v2/consultas/coren/sp/cadastro"
MAX_RETRIES = 10
RETRY_DELAY = 5  # segundos entre tentativas


class CorenService(BaseService):
    def __init__(self, token: str):
        self.token = token

    async def consultar(self, params: dict) -> dict:
        """
        Params aceitos (ao menos um obrigatório):
          - cpf: str           (ex: "01030810893")
          - inscricao: str     (ex: "17168")
          - nome_completo: str (ex: "MARCIA NAPOLEAO ALVES")

        Código 600 = erro transitório da Infosimples → retenta automaticamente.
        """
        query = {
            "token": self.token,
            "timeout": 115,
            "ignore_site_receipt": 0,
        }
        for field in ("cpf", "inscricao", "nome_completo"):
            if params.get(field):
                query[field] = params[field]

        attempt = 0
        last_data = None
        while True:
            attempt += 1
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    response = await client.get(INFOSIMPLES_URL, params=query)
                    response.raise_for_status()
                    data = response.json()
            except Exception as e:
                if attempt >= MAX_RETRIES:
                    logger.warning("COREN: erro após %d tentativas: %s", attempt, e)
                    raise
                logger.warning("COREN: erro na tentativa %d/%d (%s), aguardando %ds...", attempt, MAX_RETRIES, e, RETRY_DELAY)
                await asyncio.sleep(RETRY_DELAY)
                continue

            last_data = data
            code = data.get("code")
            if code not in (600, 605):
                return data

            if attempt >= MAX_RETRIES:
                logger.warning("COREN: código %d após %d tentativas, desistindo.", code, attempt)
                return data

            logger.warning("COREN: código %d (tentativa %d/%d), aguardando %ds...", code, attempt, MAX_RETRIES, RETRY_DELAY)
            await asyncio.sleep(RETRY_DELAY)

import base64
import time
import httpx
import os
from services.base import BaseService

TOKEN_URL = "https://gateway.apiserpro.serpro.gov.br/token"

URLS = {
    "production": {
        "pf-facial":   "https://gateway.apiserpro.serpro.gov.br/datavalid/v4/pf-facial",
        "pf-completa": "https://gateway.apiserpro.serpro.gov.br/datavalid/v4/pf-completa",
        "pf-basica":   "https://gateway.apiserpro.serpro.gov.br/datavalid/v4/pf-basica",
    },
    "demo": {
        "pf-facial":   "https://gateway.apiserpro.serpro.gov.br/datavalid-demonstracao/v4/pf-facial",
        "pf-completa": "https://gateway.apiserpro.serpro.gov.br/datavalid-demonstracao/v4/pf-completa",
        "pf-basica":   "https://gateway.apiserpro.serpro.gov.br/datavalid-demonstracao/v4/pf-basica",
    },
}


class DataValidService(BaseService):
    def __init__(self, consumer_key: str, consumer_secret: str, env: str = "production", demo_token: str | None = None):
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.env = env
        self.demo_token = demo_token
        self._token: str | None = None
        self._token_expires_at: float = 0

    def _basic_auth(self) -> str:
        credentials = f"{self.consumer_key}:{self.consumer_secret}"
        return base64.b64encode(credentials.encode()).decode()

    async def _get_token(self) -> str:
        """No ambiente demo usa token fixo. Em produção faz o fluxo client_credentials."""
        if self.env == "demo" and self.demo_token:
            return self.demo_token

        if self._token and time.time() < self._token_expires_at:
            return self._token

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                TOKEN_URL,
                headers={
                    "Authorization": f"Basic {self._basic_auth()}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials"},
            )
            response.raise_for_status()
            data = response.json()
            self._token = data["access_token"]
            self._token_expires_at = time.time() + 55 * 60
            return self._token

    async def consultar(self, params: dict, endpoint: str = "pf-completa") -> dict:
        """
        Params obrigatórios:
          - cpf: str              (ex: "25774435016")
          - foto: str             (base64 da imagem JPEG/PNG)

        Params opcionais de validação:
          - nome: str
          - data_nascimento: str  (ex: "2001-01-01")
          - nome_mae: str
          - nome_pai: str

        endpoint: "pf-completa" (default) | "pf-facial" | "pf-basica"
        """
        token = await self._get_token()
        url = URLS[self.env][endpoint]

        body: dict = {"cpf": params["cpf"]}

        if params.get("foto"):
            body["foto"] = params["foto"]

        validacao: dict = {}
        for field in ("nome", "data_nascimento", "nome_mae", "nome_pai"):
            if params.get(field):
                validacao[field] = params[field]
        if params.get("cnh"):
            validacao["cnh"] = params["cnh"]
        if params.get("endereco"):
            validacao["endereco"] = params["endereco"]
        if validacao:
            body["validacao"] = validacao

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            if not response.is_success:
                raise Exception(f"HTTP {response.status_code}: {response.text}")
            return response.json()

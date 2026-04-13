import os
from datetime import datetime, timedelta, timezone

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
JWT_SECRET    = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise ValueError("A variável de ambiente JWT_SECRET não foi configurada. Defina-a no arquivo .env ou no ambiente de produção.")
JWT_ALGORITHM = "HS256"
TOKEN_HOURS   = 8  # token válido por 1 dia de trabalho

# redirect_uri especial para fluxo popup (authorization code via JS)
GOOGLE_REDIRECT_URI = "postmessage"


def verificar_google_token(credential: str) -> dict:
    """Verifica o credential JWT emitido pelo Google Identity Services (fluxo legado)."""
    from google.oauth2 import id_token
    from google.auth.transport import requests as g_req
    return id_token.verify_oauth2_token(credential, g_req.Request(), GOOGLE_CLIENT_ID)


async def trocar_code_por_tokens(code: str) -> dict:
    """
    Troca o authorization code (vindo do popup OAuth2) por access_token + refresh_token.
    Retorna o JSON completo da resposta do Google.
    """
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
    resp.raise_for_status()
    return resp.json()


def decodificar_id_token(id_token_str: str) -> dict:
    """Decodifica o id_token retornado junto com o access_token (sem verificar assinatura — já vem do Google)."""
    import base64, json as _json
    parts = id_token_str.split(".")
    payload = parts[1] + "=="  # padding
    return _json.loads(base64.urlsafe_b64decode(payload))


def criar_jwt(email: str, nome: str, foto: str | None = None,
              role: str = "fisio", fisio_email: str | None = None,
              fisio_nome: str | None = None) -> str:
    """Cria um JWT de sessão para o app.
    role: 'fisio' (padrão) ou 'secretaria'
    fisio_email: preenchido quando role='secretaria' — indica o fisio vinculado
    fisio_nome: nome do fisioterapeuta vinculado (usado no atestado gerado pela secretaria)
    """
    import jwt
    payload = {
        "sub": email,
        "nome": nome,
        "foto": foto,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS),
    }
    if fisio_email:
        payload["fisio_email"] = fisio_email
    if fisio_nome:
        payload["fisio_nome"] = fisio_nome
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verificar_jwt(token: str) -> dict:
    """Verifica e decodifica um JWT de sessão. Lança exceção se inválido/expirado."""
    import jwt
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

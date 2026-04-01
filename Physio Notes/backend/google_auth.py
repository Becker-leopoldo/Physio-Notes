import os
from datetime import datetime, timedelta, timezone

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
JWT_SECRET = os.getenv("JWT_SECRET", "physio-notes-dev-secret-mude-em-producao")
JWT_ALGORITHM = "HS256"
TOKEN_HOURS = 72  # token válido por 3 dias


def verificar_google_token(credential: str) -> dict:
    """Verifica o credential JWT emitido pelo Google Identity Services."""
    from google.oauth2 import id_token
    from google.auth.transport import requests as g_req
    return id_token.verify_oauth2_token(credential, g_req.Request(), GOOGLE_CLIENT_ID)


def criar_jwt(email: str, nome: str, foto: str | None = None) -> str:
    """Cria um JWT de sessão para o app."""
    import jwt
    payload = {
        "sub": email,
        "nome": nome,
        "foto": foto,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verificar_jwt(token: str) -> dict:
    """Verifica e decodifica um JWT de sessão. Lança exceção se inválido/expirado."""
    import jwt
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

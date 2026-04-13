import logging
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, UploadFile, File, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import hmac
import hashlib
import aiofiles

import database as db
import transcribe
import ai
import google_auth
import notifications
import calendar_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("physio_notes")

# ---------- Constants ----------
AUTH_BEARER_PREFIX = "Bearer "
ERR_NOT_AUTHENTICATED = "Não autenticado"
ERR_PACIENTE_NOT_FOUND = "Paciente não encontrado"
ERR_SESSAO_NOT_FOUND = "Sessão não encontrada"
ERR_ACCESS_DENIED_SEC = "Acesso restrito à secretaria"
ERR_AUDIO_VAZIO = "Arquivo de áudio vazio"
ERR_TRANSCRICAO_VAZIA = "Transcrição vazia"
AUDIO_DEFAULT_FILENAME = "audio.webm"
GCAL_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SEM_TITULO = "(sem título)"
TZ_SAO_PAULO = "America/Sao_Paulo"

# ---------- WebAuthn session store (in-memory) ----------
_challenges: dict[str, bytes] = {}   # username -> challenge bytes
_sessions: dict[str, str] = {}       # token -> username
_USERNAME = "fisioterapeuta"          # usuário fixo para o MVP

# WebAuthn: auto-detecta domínio pelo request (funciona em localhost e cloud sem config)
# Se estiver atrás de proxy reverso que não repassa o host correto, defina no .env:
#   WEBAUTHN_ORIGIN=https://physio-notes.upitservices.com.br
_WEBAUTHN_ORIGIN_OVERRIDE = os.environ.get("WEBAUTHN_ORIGIN", "")

def _webauthn_origin(request: Request) -> str:
    return _WEBAUTHN_ORIGIN_OVERRIDE or str(request.base_url).rstrip("/")

def _webauthn_rp_id(request: Request) -> str:
    from urllib.parse import urlparse
    origin = _webauthn_origin(request)
    return urlparse(origin).hostname


# ---------- Lifespan ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db._migrate()
    notifications.start_scheduler()
    yield
    notifications.stop_scheduler()


app = FastAPI(title="Physio Notes API", lifespan=lifespan)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://accounts.google.com https://apis.google.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://accounts.google.com; "
        "frame-src https://accounts.google.com; "
        "object-src 'none';"
    )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:8001,http://127.0.0.1:8001,http://localhost:8000,http://127.0.0.1:8000").split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# ---------- Auth middleware ----------
# Rotas que NÃO precisam de token (auth + arquivos estáticos)
_ROTAS_PUBLICAS = {
    "/auth/config", "/auth/google-login",
    "/auth/register/begin", "/auth/register/complete",
    "/auth/login/begin", "/auth/login/complete", "/auth/status",
    "/push/vapid-public-key",
}
_PREFIXOS_PUBLICOS = ("/login", "/admin", "/manifest", "/sw.", "/icon", "/favicon", "/.well-known", "/secretaria/")

@app.middleware("http")
async def verificar_autenticacao(request: Request, call_next):
    path = request.url.path
    # Libera requisições de preflight (OPTIONS), arquivos estáticos e rotas públicas
    if (request.method == "OPTIONS"
            or path in _ROTAS_PUBLICAS
            or any(path.startswith(p) for p in _PREFIXOS_PUBLICOS)
            or path in ("/", "/index.html")):
        return await call_next(request)

    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith(AUTH_BEARER_PREFIX):
        token = auth_header.split(" ", 1)[1]
        # Aceita JWT do Google SSO
        try:
            google_auth.verificar_jwt(token)
        except Exception as e:
            logger.warning(f"verificar_autenticacao: FALHA JWT no path {path} [{request.method}]: {e}")
            return JSONResponse(status_code=401, content={"detail": ERR_NOT_AUTHENTICATED})

        # Se o token é válido, segue para o próximo handler
        return await call_next(request)
        # Aceita sessão WebAuthn (compatibilidade com usuário existente)
        if token in _sessions:
            return await call_next(request)

    logger.warning(f"verificar_autenticacao: BLOQUEIO 401 no path {path} [{request.method}] - AuthHeader: {bool(auth_header)}")
    return JSONResponse(status_code=401, content={"detail": ERR_NOT_AUTHENTICATED})


# ---------- Schemas ----------

class PacienteCreate(BaseModel):
    nome: str
    data_nascimento: str | None = None
    observacoes: str | None = None
    anamnese: str | None = None
    data_atendimento: str | None = None
    cpf: str | None = None
    endereco: str | None = None


class SessaoCreate(BaseModel):
    paciente_id: int


class PerguntaBody(BaseModel):
    pergunta: str


class ExtrairPacienteBody(BaseModel):
    transcricao: str


class ExtrairPacoteBody(BaseModel):
    transcricao: str


class ComplementarAnamneseBody(BaseModel):
    transcricao: str



class PacoteCreate(BaseModel):
    total_sessoes: int
    pago: bool = True
    valor_pago: float | None = None
    data_pagamento: str | None = None
    descricao: str | None = None


class ProcedimentoCreate(BaseModel):
    descricao: str
    valor: float | None = None
    data: str | None = None


class ProcedimentoUpdate(BaseModel):
    descricao: str
    valor: float | None = None


class ExtrairProcedimentoBody(BaseModel):
    transcricao: str


class NotaFiscalCreate(BaseModel):
    paciente_id: int | None = None
    paciente_nome: str
    valor_servico: float
    descricao: str
    competencia: str | None = None  # YYYY-MM
    # dados extras para compor o "fake NFS-e"
    prestador_razao: str | None = None
    prestador_cnpj: str | None = None
    tomador_cpf: str | None = None
    tomador_endereco: str | None = None
    iss_aliquota: float | None = None  # percentual, ex: 2.5


# ---------- Helper de autenticação ----------

def _client_ip(request: Request) -> str | None:
    """Extrai o IP do cliente de forma segura contra Spoofing.
    A leitura manual do X-Forwarded-For foi removida para evitar que o cliente 
    inverta logs de auditoria ou contorne limites.
    Em produção sob proxy (como NGINX/Alb), configure o uvicorn com --proxy-headers."""
    return request.client.host if request.client else None


def _owner_email(request: Request) -> str | None:
    """Extrai email do JWT. Retorna None para sessões WebAuthn (legado)."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1]
        if token in _sessions:
            val = _sessions[token]
            return val if "@" in val else None  # só retorna se for email válido
        try:
            payload = google_auth.verificar_jwt(token)
            return payload.get("sub")
        except Exception as e:
            logger.info("_owner_email: JWT inválido: %s", e)
    return None


def _sec_context(request: Request) -> tuple[str, str]:
    """Extrai (secretaria_email, fisio_email) do JWT de secretaria.
    Lança 403 se o token não for de secretaria."""
    auth = request.headers.get("authorization", "")
    if auth.startswith(AUTH_BEARER_PREFIX):
        try:
            token = auth.split(" ", 1)[1]
            payload = google_auth.verificar_jwt(token)
            if payload.get("role") == "secretaria" and payload.get("fisio_email"):
                return payload["sub"], payload["fisio_email"]
            else:
                logger.warning(f"_sec_context: Role ou fisio_email inválidos. Role: {payload.get('role')}")
        except Exception as e:
            logger.error(f"_sec_context: Erro ao verificar JWT: {e}")
            pass
    logger.warning(f"_sec_context: BLOQUEIO 403 para {request.url.path} [{request.method}] - Header: {bool(auth)}")
    raise HTTPException(status_code=403, detail=ERR_ACCESS_DENIED_SEC)


def _verificar_dono(paciente: dict, owner: str | None):
    """Lança 404 se o paciente não pertence ao usuário (None = WebAuthn, vê tudo)."""
    if owner and paciente.get("owner_email") and paciente["owner_email"] != owner:
        raise HTTPException(status_code=404, detail=ERR_PACIENTE_NOT_FOUND)


def _verificar_dono_sessao(sessao: dict, owner: str | None):
    if not owner:
        return
    paciente = db.get_paciente(sessao["paciente_id"])
    if paciente:
        _verificar_dono(paciente, owner)


def _verificar_dono_documento(doc: dict, owner: str | None):
    if not owner:
        return
    paciente = db.get_paciente(doc["paciente_id"])
    if paciente:
        _verificar_dono(paciente, owner)


# ---------- Pacientes ----------

@app.post("/pacientes", status_code=201, responses={409: {"description": "Conflict"}})
def criar_paciente(body: PacienteCreate, request: Request):
    owner = _owner_email(request)
    try:
        paciente = db.criar_paciente(body.nome, body.data_nascimento, body.observacoes, body.anamnese, body.cpf, body.endereco, owner)
    except (sqlite3.IntegrityError, ValueError):
        db.registrar_audit(owner, "paciente_criar_cpf_duplicado", f"nome={body.nome}", _client_ip(request))
        raise HTTPException(status_code=409, detail="Paciente com este CPF já cadastrado na sua conta.")
    db.registrar_audit(owner, "paciente_criar", f"id={paciente['id']} nome={paciente['nome']}", _client_ip(request))
    return paciente


@app.get("/pacientes")
def listar_pacientes(request: Request):
    return db.listar_pacientes(_owner_email(request))


@app.get("/pacientes/{paciente_id}", responses={404: {"description": "Not Found"}})
def buscar_paciente(paciente_id: int, request: Request):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, _owner_email(request))
    return paciente


class PacienteUpdate(BaseModel):
    nome: str
    data_nascimento: str | None = None
    observacoes: str | None = None
    anamnese: str | None = None
    cpf: str | None = None
    endereco: str | None = None
    conduta_tratamento: str | None = None


@app.delete("/pacientes/{paciente_id}", status_code=204, responses={404: {"description": "Not Found"}})
def deletar_paciente(paciente_id: int, request: Request):
    owner = _owner_email(request)
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, owner)
    db.deletar_paciente(paciente_id)
    db.registrar_audit(owner, "paciente_deletar", f"id={paciente_id} nome={paciente['nome']}", _client_ip(request))


@app.put("/pacientes/{paciente_id}", responses={404: {"description": "Not Found"}, 409: {"description": "Conflict"}})
def atualizar_paciente(paciente_id: int, body: PacienteUpdate, request: Request):
    owner = _owner_email(request)
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, owner)
    try:
        resultado = db.atualizar_paciente(paciente_id, body.nome, body.data_nascimento, body.anamnese, body.cpf, body.endereco, body.conduta_tratamento)
    except (sqlite3.IntegrityError, ValueError):
        raise HTTPException(status_code=409, detail="Paciente com este CPF já cadastrado na sua conta.")
    db.registrar_audit(owner, "paciente_atualizar", f"id={paciente_id}", _client_ip(request))
    return resultado


async def _disparar_ia_pos_anamnese(paciente_id: int, anamnese: str, conduta_atual: str | None, owner: str | None):
    """
    Background task: gera/atualiza check clínico (sugestao_ia).
    Conduta é gerada de forma síncrona no endpoint — não repetir aqui.
    Nunca levanta exceção.
    """
    import json as _json
    try:
        sessoes = db.get_historico_paciente(paciente_id)
        _pac_nome = (db.get_paciente(paciente_id) or {}).get("nome")
        sugestao = await ai.gerar_sugestao_paciente(anamnese, sessoes, owner, paciente_nome=_pac_nome)
        db.salvar_sugestao_ia(paciente_id, _json.dumps(sugestao, ensure_ascii=False))
    except Exception as exc:
        logger.warning("_disparar_ia_pos_anamnese: paciente_id=%s: %s", paciente_id, exc)


@app.post("/pacientes/{paciente_id}/complementar-anamnese", responses={404: {"description": "Not Found"}})
@limiter.limit("20/minute")
async def complementar_anamnese(paciente_id: int, body: ComplementarAnamneseBody, background_tasks: BackgroundTasks, request: Request):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    owner = _owner_email(request)
    _verificar_dono(paciente, owner)
    anamnese_atualizada = await ai.complementar_anamnese(body.transcricao, paciente.get("anamnese"), owner)
    conduta_atual = paciente.get("conduta_tratamento")
    conduta_gerada = None
    if not conduta_atual:
        try:
            conduta_gerada = await ai.sugerir_conduta(anamnese_atualizada, owner)
        except Exception as exc:
            logger.warning("complementar_anamnese: conduta fallback: paciente_id=%s: %s", paciente_id, exc)
    conduta_final = conduta_gerada or conduta_atual
    paciente_atualizado = db.atualizar_paciente(
        paciente_id, paciente["nome"], paciente.get("data_nascimento"),
        anamnese_atualizada, paciente.get("cpf"), paciente.get("endereco"),
        conduta_final,
    )
    background_tasks.add_task(_disparar_ia_pos_anamnese, paciente_id, anamnese_atualizada, conduta_final, owner)
    return {
        "anamnese": paciente_atualizado["anamnese"],
        "conduta_tratamento": paciente_atualizado.get("conduta_tratamento"),
        "conduta_gerada": conduta_gerada is not None,
    }


class ComplementarCondutaBody(BaseModel):
    transcricao: str


@app.post("/pacientes/{paciente_id}/complementar-conduta", responses={404: {"description": "Not Found"}})
@limiter.limit("20/minute")
async def complementar_conduta(paciente_id: int, body: ComplementarCondutaBody, request: Request):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, _owner_email(request))
    conduta_atualizada = await ai.complementar_conduta(body.transcricao, paciente.get("conduta_tratamento"), _owner_email(request))
    paciente_atualizado = db.atualizar_paciente(
        paciente_id, paciente["nome"], paciente.get("data_nascimento"),
        paciente.get("anamnese"), paciente.get("cpf"), paciente.get("endereco"),
        conduta_atualizada,
    )
    return {"conduta_tratamento": paciente_atualizado["conduta_tratamento"]}


@app.post("/pacientes/{paciente_id}/sugerir-conduta", responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}})
async def sugerir_conduta(paciente_id: int, request: Request):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, _owner_email(request))
    if not paciente.get("anamnese"):
        raise HTTPException(status_code=400, detail="Paciente não possui anamnese registrada. Registre a anamnese primeiro.")
    sugestao = await ai.sugerir_conduta(paciente["anamnese"], _owner_email(request))
    return {"sugestao": sugestao}


@app.post("/pacientes/{paciente_id}/gerar-sugestao", responses={404: {"description": "Not Found"}})
async def gerar_sugestao(paciente_id: int, request: Request):
    """Atualiza a sugestão da IA para o paciente (botão 'Atualizar' no card)."""
    import json as _json
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    owner = _owner_email(request)
    _verificar_dono(paciente, owner)
    sessoes = db.get_historico_paciente(paciente_id)
    sugestao = await ai.gerar_sugestao_paciente(paciente.get("anamnese") or "", sessoes, owner, paciente_nome=paciente.get("nome"))
    sugestao_json = _json.dumps(sugestao, ensure_ascii=False)
    db.salvar_sugestao_ia(paciente_id, sugestao_json)
    return {"sugestao_ia": sugestao, "sugestao_ia_em": db.get_paciente(paciente_id).get("sugestao_ia_em")}


class SalvarAnamneseManualBody(BaseModel):
    texto: str

@app.post("/pacientes/{paciente_id}/salvar-anamnese-manual", responses={404: {"description": "Not Found"}})
async def salvar_anamnese_manual(paciente_id: int, body: SalvarAnamneseManualBody, background_tasks: BackgroundTasks, request: Request):
    """
    Formata anamnese com IA, salva.
    Se conduta estiver vazia, gera conduta de forma síncrona e retorna ambas.
    Check clínico fica em background.
    """
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    owner = _owner_email(request)
    _verificar_dono(paciente, owner)
    texto = body.texto.strip()
    if not texto:
        db.atualizar_paciente(paciente_id, paciente["nome"], paciente.get("data_nascimento"), None, paciente.get("cpf"), paciente.get("endereco"), paciente.get("conduta_tratamento"))
        return {"anamnese": None, "conduta_tratamento": paciente.get("conduta_tratamento")}

    # 1. Formata anamnese
    anamnese_formatada = await ai.formatar_anamnese_texto(texto, owner)

    # 2. Gera conduta se vazia (síncrono — retorna para o frontend)
    conduta_atual = paciente.get("conduta_tratamento")
    conduta_gerada = None
    if not conduta_atual:
        try:
            conduta_gerada = await ai.sugerir_conduta(anamnese_formatada, owner)
        except Exception as exc:
            logger.warning("salvar_anamnese_manual[conduta]: %s", exc)

    conduta_final = conduta_gerada or conduta_atual
    db.atualizar_paciente(paciente_id, paciente["nome"], paciente.get("data_nascimento"), anamnese_formatada, paciente.get("cpf"), paciente.get("endereco"), conduta_final)

    # 3. Check clínico em background
    background_tasks.add_task(_disparar_ia_pos_anamnese, paciente_id, anamnese_formatada, conduta_final, owner)

    return {"anamnese": anamnese_formatada, "conduta_tratamento": conduta_final, "conduta_gerada": conduta_gerada is not None}


@app.post("/pacientes/{paciente_id}/formatar-conduta", responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}})
async def formatar_conduta(paciente_id: int, body: ComplementarCondutaBody, request: Request):
    """Formata texto livre de conduta com tópicos via IA, sem alterar conteúdo."""
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, _owner_email(request))
    if not body.transcricao.strip():
        raise HTTPException(status_code=400, detail="Texto não pode ser vazio")
    formatado = await ai.formatar_conduta_texto(body.transcricao, _owner_email(request))
    return {"conduta_formatada": formatado}


@app.post("/pacientes/{paciente_id}/sugestao-dia", responses={404: {"description": "Not Found"}})
async def sugestao_do_dia(paciente_id: int, request: Request):
    """Gera sugestão prática do que fazer na sessão de hoje."""
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    owner = _owner_email(request)
    _verificar_dono(paciente, owner)
    sessoes = db.get_historico_paciente(paciente_id)
    sugestao = await ai.sugestao_do_dia(
        paciente.get("anamnese") or "",
        paciente.get("conduta_tratamento"),
        sessoes,
        owner,
        paciente_nome=paciente.get("nome"),
    )
    return {"sugestao": sugestao}


@app.post("/pacientes/{paciente_id}/feedback-clinico", responses={404: {"description": "Not Found"}})
async def feedback_clinico(paciente_id: int, request: Request):
    """Gera feedback clínico sutil ao fisio sobre pendências e itens não abordados."""
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    owner = _owner_email(request)
    _verificar_dono(paciente, owner)
    sessoes = db.get_historico_paciente(paciente_id)
    feedback = await ai.feedback_clinico(
        paciente.get("anamnese") or "",
        paciente.get("conduta_tratamento"),
        sessoes,
        owner,
        paciente_nome=paciente.get("nome"),
    )
    return {"feedback": feedback}


@app.post("/transcrever", responses={400: {"description": "Bad Request"}, 502: {"description": "Bad Gateway"}})
@limiter.limit("20/minute")
async def transcrever_audio_avulso(request: Request, audio: Annotated[UploadFile, File()]):
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail=ERR_AUDIO_VAZIO)
    try:
        transcricao = await transcribe.transcrever_audio(audio_bytes, audio.filename or AUDIO_DEFAULT_FILENAME)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na transcrição: {str(e)}")
    return {"transcricao": transcricao}


@app.post("/extrair-paciente", responses={400: {"description": "Bad Request"}, 502: {"description": "Bad Gateway"}})
@limiter.limit("20/minute")
async def extrair_dados_paciente(body: ExtrairPacienteBody, request: Request):
    if not body.transcricao.strip():
        raise HTTPException(status_code=400, detail=ERR_TRANSCRICAO_VAZIA)
    try:
        dados = await ai.extrair_dados_paciente(body.transcricao, _owner_email(request))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na extração: {str(e)}")
    return dados


@app.post("/extrair-procedimento", responses={400: {"description": "Bad Request"}, 502: {"description": "Bad Gateway"}})
@limiter.limit("20/minute")
async def extrair_procedimento(body: ExtrairProcedimentoBody, request: Request):
    if not body.transcricao.strip():
        raise HTTPException(status_code=400, detail=ERR_TRANSCRICAO_VAZIA)
    try:
        dados = await ai.extrair_procedimento(body.transcricao, _owner_email(request))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na extração: {str(e)}")
    return dados


@app.post("/sessoes/{sessao_id}/detectar-procedimentos", responses={404: {"description": "Not Found"}, 502: {"description": "Bad Gateway"}})
async def detectar_procedimentos(sessao_id: int, request: Request):
    """
    Analisa transcrição + nota da sessão com IA e retorna sugestões de
    procedimentos extras detectados. NÃO salva automaticamente — retorna
    as sugestões para o frontend confirmar.
    """
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail=ERR_SESSAO_NOT_FOUND)
    _verificar_dono_sessao(sessao, _owner_email(request))

    chunks = db.get_chunks_sessao(sessao_id)
    consolidado = db.get_consolidado_sessao(sessao_id)

    transcricao = "\n".join(c["transcricao"] for c in chunks if c.get("transcricao"))
    if not transcricao.strip():
        return {"sugestoes": []}

    nota = consolidado.get("nota") or consolidado.get("conduta") if consolidado else None

    try:
        sugestoes = await ai.detectar_procedimentos_extras(transcricao, nota, _owner_email(request))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na IA: {str(e)}")

    # Filtrar sugestões que já foram salvas (comparação por descrição normalizada)
    ja_salvos = {p["descricao"].strip().lower() for p in db.get_procedimentos_sessao(sessao_id)}
    sugestoes = [s for s in sugestoes if s.get("descricao", "").strip().lower() not in ja_salvos]

    return {"sugestoes": sugestoes}


@app.get("/sessoes/{sessao_id}/procedimentos", responses={404: {"description": "Not Found"}})
def listar_procedimentos(sessao_id: int, request: Request):
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail=ERR_SESSAO_NOT_FOUND)
    _verificar_dono_sessao(sessao, _owner_email(request))
    return db.get_procedimentos_sessao(sessao_id)


@app.post("/sessoes/{sessao_id}/procedimentos", responses={404: {"description": "Not Found"}})
def criar_procedimento(sessao_id: int, body: ProcedimentoCreate, request: Request):
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail=ERR_SESSAO_NOT_FOUND)
    _verificar_dono_sessao(sessao, _owner_email(request))
    return db.adicionar_procedimento(sessao_id, sessao["paciente_id"], body.descricao, body.valor, body.data)


@app.put("/procedimentos/{proc_id}")
def atualizar_procedimento(proc_id: int, body: ProcedimentoUpdate, request: Request):
    db.atualizar_procedimento(proc_id, body.descricao, body.valor)
    return {"ok": True}


@app.delete("/procedimentos/{proc_id}")
def deletar_procedimento(proc_id: int):
    db.deletar_procedimento(proc_id)
    return {"ok": True}


@app.post("/extrair-pacote", responses={400: {"description": "Bad Request"}, 502: {"description": "Bad Gateway"}})
@limiter.limit("20/minute")
async def extrair_dados_pacote(body: ExtrairPacoteBody, request: Request):
    if not body.transcricao.strip():
        raise HTTPException(status_code=400, detail=ERR_TRANSCRICAO_VAZIA)
    try:
        dados = await ai.extrair_dados_pacote(body.transcricao, _owner_email(request))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na extração: {str(e)}")
    return dados



# ---------- Sessoes ----------

@app.post("/sessoes", status_code=201, responses={404: {"description": "Not Found"}, 409: {"description": "Conflict"}})
def criar_sessao(body: SessaoCreate, request: Request):
    owner = _owner_email(request)
    paciente = db.get_paciente(body.paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")

    aberta = db.sessao_aberta_do_paciente(body.paciente_id)
    if aberta:
        raise HTTPException(
            status_code=409,
            detail="Já existe uma sessão aberta para este paciente. Encerre-a antes de iniciar uma nova.",
        )

    sessao = db.criar_sessao(body.paciente_id)
    db.registrar_audit(owner, "sessao_criar", f"id={sessao['id']} paciente_id={body.paciente_id}", _client_ip(request))
    return sessao


@app.get("/sessoes/{sessao_id}", responses={404: {"description": "Not Found"}})
def buscar_sessao(sessao_id: int, request: Request):
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail=ERR_SESSAO_NOT_FOUND)
    _verificar_dono_sessao(sessao, _owner_email(request))

    chunks = db.get_chunks_sessao(sessao_id)
    consolidado = db.get_consolidado_sessao(sessao_id)

    return {**sessao, "chunks": chunks, "consolidado": consolidado}


@app.post("/sessoes/{sessao_id}/audio", responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}, 502: {"description": "Bad Gateway"}})
@limiter.limit("20/minute")
async def upload_audio(sessao_id: int, audio: Annotated[UploadFile, File()], request: Request = None):
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail=ERR_SESSAO_NOT_FOUND)
    _verificar_dono_sessao(sessao, _owner_email(request))
    if sessao["status"] != "aberta":
        raise HTTPException(status_code=400, detail="Sessão já encerrada")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail=ERR_AUDIO_VAZIO)

    try:
        transcricao = await transcribe.transcrever_audio(audio_bytes, audio.filename or AUDIO_DEFAULT_FILENAME)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na transcrição: {str(e)}")

    chunk = db.add_audio_chunk(sessao_id, transcricao)
    return {"chunk": chunk, "transcricao": transcricao}


class CancelamentoBody(BaseModel):
    cobrar: bool = True
    valor: float | None = None
    complemento: str | None = None


@app.post("/sessoes/{sessao_id}/cancelar-com-cobranca", responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}})
def cancelar_com_cobranca(sessao_id: int, body: CancelamentoBody, request: Request):
    owner = _owner_email(request)
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail=ERR_SESSAO_NOT_FOUND)
    _verificar_dono_sessao(sessao, owner)
    if sessao["status"] != "aberta":
        raise HTTPException(status_code=400, detail="Sessão não está aberta")
    db.registrar_cancelamento(sessao_id, body.cobrar, body.valor, body.complemento, owner)
    db.registrar_audit(owner, "sessao_cancelar", f"id={sessao_id} cobrar={body.cobrar}", _client_ip(request))
    return {"status": "cancelada", "sessao_id": sessao_id}


@app.delete("/sessoes/{sessao_id}", responses={404: {"description": "Not Found"}})
def cancelar_sessao(sessao_id: int, request: Request):
    owner = _owner_email(request)
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail=ERR_SESSAO_NOT_FOUND)
    _verificar_dono_sessao(sessao, owner)
    chunks = db.get_chunks_sessao(sessao_id)
    # Sessão aberta sem áudio → cancela (hard delete)
    if sessao["status"] == "aberta" and not chunks:
        db.cancelar_sessao(sessao_id)
        db.registrar_audit(owner, "sessao_deletar", f"id={sessao_id} tipo=hard_delete", _client_ip(request))
        return {"status": "cancelada", "sessao_id": sessao_id}
    # Qualquer outro caso → soft delete
    db.deletar_sessao(sessao_id)
    db.registrar_audit(owner, "sessao_deletar", f"id={sessao_id} tipo=soft_delete", _client_ip(request))
    return {"status": "deletada", "sessao_id": sessao_id}


@app.post("/sessoes/{sessao_id}/adicionar-audio", responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}, 502: {"description": "Bad Gateway"}})
async def adicionar_audio_sessao_encerrada(sessao_id: int, audio: Annotated[UploadFile, File()], request: Request = None):
    """Adiciona áudio a uma sessão encerrada do mesmo dia, sem abater do pacote."""
    from datetime import date
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail=ERR_SESSAO_NOT_FOUND)
    _verificar_dono_sessao(sessao, _owner_email(request))
    if sessao["status"] == "aberta":
        raise HTTPException(status_code=400, detail="Use o endpoint /audio para sessões abertas")
    if sessao["data"] != date.today().isoformat():
        raise HTTPException(status_code=400, detail="Só é possível adicionar notas em sessões do dia atual")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail=ERR_AUDIO_VAZIO)

    try:
        transcricao = await transcribe.transcrever_audio(audio_bytes, audio.filename or AUDIO_DEFAULT_FILENAME)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na transcrição: {str(e)}")

    chunk = db.add_audio_chunk(sessao_id, transcricao)

    # Re-consolida com todos os chunks (sem deducao de pacote)
    chunks = db.get_chunks_sessao(sessao_id)
    try:
        _pac_nome = (db.get_paciente(sessao["paciente_id"]) or {}).get("nome")
        dados = await ai.consolidar_sessao([c["transcricao"] for c in chunks], _owner_email(request), paciente_nome=_pac_nome)
        db.salvar_consolidado(sessao_id, dados)
    except Exception as e:
        logger.warning("adicionar_audio: falha ao re-consolidar sessao_id=%s: %s", sessao_id, e)

    return {"chunk": chunk, "transcricao": transcricao}


class EncerrarBody(BaseModel):
    cobrar: bool = True
    valor: float | None = None


@app.post("/sessoes/{sessao_id}/encerrar", responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}, 409: {"description": "Conflict"}, 502: {"description": "Bad Gateway"}})
@limiter.limit("10/minute")
async def encerrar_sessao(sessao_id: int, body: EncerrarBody = None, request: Request = None, background_tasks: BackgroundTasks = None):
    if body is None:
        body = EncerrarBody()
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail=ERR_SESSAO_NOT_FOUND)
    _verificar_dono_sessao(sessao, _owner_email(request))
    if sessao["status"] != "aberta":
        raise HTTPException(status_code=400, detail="Sessão já está encerrada")

    chunks = db.get_chunks_sessao(sessao_id)
    if not chunks:
        raise HTTPException(
            status_code=400,
            detail="Nenhum áudio registrado nesta sessão. Grave pelo menos um áudio antes de encerrar.",
        )

    transcricoes = [c["transcricao"] for c in chunks]
    owner = _owner_email(request)
    _pac_enc = (db.get_paciente(sessao["paciente_id"]) or {}).get("nome")

    try:
        dados_consolidados = await ai.consolidar_sessao(transcricoes, owner, paciente_nome=_pac_enc)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao consolidar com IA: {str(e)}")

    consolidado = db.salvar_consolidado(sessao_id, dados_consolidados)

    # Tenta extrair valor do áudio (sempre — útil para pré-preencher prompt no frontend)
    transcricao_completa = "\n".join(transcricoes)
    valor_override = body.valor
    valor_ai_detectado = None
    if not valor_override:
        try:
            valor_ai_detectado = await ai.extrair_valor_sessao(transcricao_completa, owner)
            if body.cobrar:
                valor_override = valor_ai_detectado
        except Exception:
            pass

    resultado_encerramento = db.encerrar_sessao(sessao_id, owner, cobrar=body.cobrar, valor_override=valor_override)

    if resultado_encerramento.get("_ja_encerrada"):
        raise HTTPException(status_code=409, detail="Sessão já foi encerrada.")

    # Detecção automática de procedimentos extras na transcrição
    nota = dados_consolidados.get("nota") or dados_consolidados.get("conduta")
    try:
        extras = await ai.detectar_procedimentos_extras(transcricao_completa, nota, owner)
        for item in extras:
            db.adicionar_procedimento(
                sessao_id, sessao["paciente_id"],
                item["descricao"], item.get("valor"), None,
            )
    except Exception as e:
        logger.warning("encerrar_sessao: falha ao detectar procedimentos extras sessao_id=%s: %s", sessao_id, e)

    # Notificação: pacote quase acabando (≤ 2 sessões restantes)
    try:
        restantes = db.get_sessoes_restantes_paciente(sessao["paciente_id"])
        if restantes is not None and restantes <= 2 and owner:
            paciente_info = db.get_paciente(sessao["paciente_id"])
            notifications.notificar_pacote_quase_acabando(
                owner, paciente_info["nome"], restantes
            )
    except Exception as e:
        logger.warning("encerrar_sessao: falha ao notificar pacote sessao_id=%s: %s", sessao_id, e)

    db.registrar_audit(owner, "sessao_encerrar", f"id={sessao_id} cobrar={body.cobrar} valor={valor_override}", _client_ip(request))

    # Cria evento no Google Calendar do fisio (fire-and-forget)
    if owner and background_tasks:
        paciente_info = db.get_paciente(sessao["paciente_id"])
        resumo_notas = dados_consolidados.get("nota") or dados_consolidados.get("conduta") or ""
        background_tasks.add_task(
            calendar_service.criar_evento_sessao,
            owner,
            paciente_info["nome"] if paciente_info else "Paciente",
            sessao.get("criado_em") or "",
            resumo_notas[:500] if resumo_notas else None,
        )

    return {
        "consolidado": consolidado,
        "sessao_id": sessao_id,
        "status": "encerrada",
        "sessao_avulsa_valor": resultado_encerramento.get("sessao_avulsa_valor"),
        "valor_ai_detectado": valor_ai_detectado,
        "cobrar": body.cobrar,
    }


@app.get("/pacientes/{paciente_id}/sessoes", responses={404: {"description": "Not Found"}})
def listar_sessoes_paciente(paciente_id: int, request: Request):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, _owner_email(request))
    return db.get_sessoes_com_consolidado(paciente_id)


# ---------- Historico / IA ----------

@app.get("/faturamento/pacientes")
def faturamento_pacientes(mes: str | None = None, paciente_id: int | None = None, request: Request = None):
    return db.get_faturamento_pacientes(ano_mes=mes, paciente_id=paciente_id, owner_email=_owner_email(request))


class RecargaBody(BaseModel):
    valor_brl: float
    descricao: str | None = None

@app.post("/creditos/recarregar", status_code=201, responses={400: {"description": "Bad Request"}, 401: {"description": "Unauthorized"}})
@limiter.limit("10/minute")
def creditos_recarregar(body: RecargaBody, request: Request):
    """Registra uma recarga de créditos para o fisio logado."""
    owner = _owner_email(request)
    if not owner:
        raise HTTPException(status_code=401, detail=ERR_NOT_AUTHENTICATED)
    if body.valor_brl <= 0:
        raise HTTPException(status_code=400, detail="Valor deve ser positivo.")
    if body.valor_brl > 50_000:
        raise HTTPException(status_code=400, detail="Valor máximo por recarga é R$ 50.000,00.")
    recarga = db.registrar_recarga(owner, body.valor_brl, body.descricao)
    db.registrar_audit(owner, "credito_recarga", f"valor_brl={body.valor_brl}", _client_ip(request))
    return recarga

@app.get("/precificacao/publico")
def precificacao_publico(cotacao: float = 5.80):
    """Retorna APENAS o preço sugerido ao cliente final — sem margens, cotação ou modelo."""
    if not (1.0 <= cotacao <= 20.0):
        raise HTTPException(status_code=400, detail="Cotação fora do intervalo permitido.")
    from ai import MODEL, MODEL_PRICING
    preco_modelo = MODEL_PRICING.get(MODEL, {"input": 0.10, "output": 0.40})
    config       = db.get_config_precificacao()
    uso          = db.get_custo_medio_mensal_usd()
    custo_brl    = uso["custo_medio_usd"] * cotacao
    margem       = config["margem_pct"] / 100
    imposto      = config["imposto_pct"] / 100
    preco        = round(custo_brl * (1 + margem) * (1 + imposto), 2)
    return {"preco_mensal_brl": preco}


@app.get("/creditos/saldo", responses={400: {"description": "Bad Request"}, 401: {"description": "Unauthorized"}})
def creditos_saldo(cotacao: float = 5.80, request: Request = None):
    """Retorna saldo de créditos do fisio (total carregado - gasto em BRL)."""
    owner = _owner_email(request)
    if not owner:
        raise HTTPException(status_code=401, detail=ERR_NOT_AUTHENTICATED)
    if not (1.0 <= cotacao <= 20.0):
        raise HTTPException(status_code=400, detail="Cotação fora do intervalo permitido (1.00–20.00).")
    return db.get_creditos(owner, cotacao)


# ── Mercado Pago PIX ─────────────────────────────────────────────────────────

MP_ACCESS_TOKEN    = os.getenv("MP_ACCESS_TOKEN", "")
MP_WEBHOOK_SECRET  = os.getenv("MP_WEBHOOK_SECRET", "")
_MP_MODO_TESTE     = os.getenv("MP_MODO_TESTE", "").lower() in ("1", "true", "yes")
_MP_OPCOES         = {50: 0.05, 100: 0.10, 150: 0.15} if _MP_MODO_TESTE else {50: 50.0, 100: 100.0, 150: 150.0}

if not MP_ACCESS_TOKEN:
    logger.warning("MP_ACCESS_TOKEN não configurado — pagamentos PIX desativados.")
if not MP_WEBHOOK_SECRET:
    logger.warning("MP_WEBHOOK_SECRET não configurado — webhooks do Mercado Pago não serão validados.")
if _MP_MODO_TESTE:
    logger.critical("⚠️  MP_MODO_TESTE=true — valores de teste ativos (R$0,05). NÃO use em produção!")


class PagamentoPixBody(BaseModel):
    creditos: int   # 50, 100 ou 150


@app.post("/pagamento/pix/criar", status_code=201,
          responses={400: {"description": "Bad Request"}, 503: {"description": "Service Unavailable"}})
@limiter.limit("5/minute")
async def pagamento_pix_criar(body: PagamentoPixBody, request: Request):
    """Cria um pagamento PIX no Mercado Pago e retorna QR code."""
    owner = _owner_email(request)
    if not owner:
        raise HTTPException(status_code=401, detail=ERR_NOT_AUTHENTICATED)
    if body.creditos not in _MP_OPCOES:
        raise HTTPException(status_code=400, detail="Opções válidas: 50, 100 ou 150 créditos.")
    if not MP_ACCESS_TOKEN:
        raise HTTPException(status_code=503, detail="Pagamento via PIX não configurado no servidor.")

    import httpx
    from datetime import datetime, timezone, timedelta

    valor_brl = _MP_OPCOES[body.creditos]
    expira_em = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
    idem_key  = f"{owner}-{body.creditos}-{int(datetime.now(timezone.utc).timestamp())}"

    payload = {
        "transaction_amount": valor_brl,
        "description": f"Physio Notes — {body.creditos} créditos",
        "payment_method_id": "pix",
        "payer": {"email": owner},
        "date_of_expiration": expira_em,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.mercadopago.com/v1/payments",
                json=payload,
                headers={
                    "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
                    "Content-Type": "application/json",
                    "X-Idempotency-Key": idem_key,
                },
            )
        if resp.status_code not in (200, 201):
            logger.error(f"MP erro {resp.status_code}: {resp.text[:300]}")
            raise HTTPException(status_code=503, detail="Erro ao gerar QR code. Tente novamente.")
        mp_data = resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=503, detail="Mercado Pago não respondeu. Tente novamente.")

    payment_id = str(mp_data["id"])
    pix_data   = mp_data.get("point_of_interaction", {}).get("transaction_data", {})
    qr_code    = pix_data.get("qr_code", "")
    qr_base64  = pix_data.get("qr_code_base64", "")

    try:
        db.criar_pagamento_pix(owner, payment_id, body.creditos, valor_brl, qr_code, expira_em)
    except Exception:
        existing = db.get_pagamento_pix(payment_id)
        if existing:
            raise HTTPException(status_code=409, detail="Pagamento já registrado.")
        raise
    db.registrar_audit(owner, "pix_criado",
                       f"creditos={body.creditos} payment_id={payment_id}", _client_ip(request))

    return {
        "payment_id": payment_id,
        "qr_code": qr_code,
        "qr_code_base64": qr_base64,
        "valor_brl": valor_brl,
        "creditos": body.creditos,
        "expira_em": expira_em,
    }


@app.get("/pagamento/status/{payment_id}",
         responses={401: {"description": "Unauthorized"}, 404: {"description": "Not Found"}})
async def pagamento_status(payment_id: str, request: Request):
    """Polling de status do pagamento — consulta o MP em tempo real e credita se aprovado."""
    if not re.fullmatch(r'[\w\-]+', payment_id):
        raise HTTPException(status_code=400, detail="payment_id inválido.")
    owner = _owner_email(request)
    if not owner:
        raise HTTPException(status_code=401, detail=ERR_NOT_AUTHENTICATED)
    p = db.get_pagamento_pix_por_owner(payment_id, owner)
    if not p:
        raise HTTPException(status_code=404, detail="Pagamento não encontrado.")

    # Se já creditado, retorna direto sem consultar o MP novamente
    if p["creditado"]:
        return {"payment_id": payment_id, "status": "approved", "creditado": True}

    # Consulta status real no MP a cada poll
    if MP_ACCESS_TOKEN:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"https://api.mercadopago.com/v1/payments/{payment_id}",
                    headers={"Authorization": f"Bearer {MP_ACCESS_TOKEN}"},
                )
            if resp.status_code == 200:
                mp_status = resp.json().get("status", p["status"])
                db.atualizar_status_pagamento_pix(payment_id, mp_status)
                if mp_status == "approved":
                    db.aprovar_pagamento_pix(payment_id)
                    db.registrar_audit(owner, "pix_aprovado_polling",
                                       f"payment_id={payment_id}", None)
                    return {"payment_id": payment_id, "status": "approved", "creditado": True}
                return {"payment_id": payment_id, "status": mp_status, "creditado": False}
        except Exception as exc:
            logger.warning(f"pagamento_status: erro ao consultar MP {payment_id}: {exc}")

    return {"payment_id": payment_id, "status": p["status"], "creditado": bool(p["creditado"])}


@app.post("/pagamento/webhook", status_code=200)
@limiter.limit("10/minute")
async def pagamento_webhook(request: Request):
    """Webhook do Mercado Pago: valida assinatura, consulta status real e credita se aprovado."""
    body_bytes = await request.body()

    if MP_WEBHOOK_SECRET:
        sig_header = request.headers.get("x-signature", "")
        req_id     = request.headers.get("x-request-id", "")
        parts      = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
        data_id    = request.query_params.get("data.id", "")
        manifest   = f"id:{data_id};request-id:{req_id};ts:{parts.get('ts', '')};"
        expected   = hmac.new(MP_WEBHOOK_SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, parts.get("v1", "")):
            logger.warning("Webhook MP: assinatura inválida")
            raise HTTPException(status_code=401, detail="Assinatura inválida.")

    try:
        import json as _json
        data = _json.loads(body_bytes)
    except Exception:
        return {"ok": False}

    action     = data.get("action", "")
    payment_id = str(data.get("data", {}).get("id", ""))

    if action not in ("payment.updated", "payment.created") or not payment_id:
        return {"ok": True}

    # Consulta status real no MP — não confia apenas no payload do webhook
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.mercadopago.com/v1/payments/{payment_id}",
                headers={"Authorization": f"Bearer {MP_ACCESS_TOKEN}"},
            )
        mp_payment = resp.json()
    except Exception as exc:
        logger.error(f"Webhook: erro ao consultar MP payment {payment_id}: {exc}")
        return {"ok": False}

    status = mp_payment.get("status", "")
    db.atualizar_status_pagamento_pix(payment_id, status)

    if status == "approved":
        creditado = db.aprovar_pagamento_pix(payment_id)
        p = db.get_pagamento_pix(payment_id)
        if creditado and p:
            db.registrar_audit(p["owner_email"], "pix_aprovado",
                               f"creditos={p['creditos']} payment_id={payment_id}", "webhook")
            logger.info(f"PIX aprovado: {payment_id} → {p['owner_email']} +{p['creditos']} créditos")

    return {"ok": True}


@app.get("/billing", responses={401: {"description": "Unauthorized"}})
def billing(mes: str | None = None, request: Request = None):
    from datetime import date
    owner = _owner_email(request)
    if not owner:
        raise HTTPException(status_code=401, detail=ERR_NOT_AUTHENTICATED)
    ano_mes = mes or date.today().strftime("%Y-%m")
    link_sec = db.get_secretaria_do_fisio(owner)
    sec_email = link_sec["secretaria_email"] if link_sec and link_sec.get("status") == "ativa" else None
    return {
        "mes_atual": db.get_billing_mes(ano_mes, owner),
        "historico": db.get_billing_meses(owner),
        "secretaria_email": sec_email,
    }


@app.get("/billing/log", responses={401: {"description": "Unauthorized"}})
def billing_log(mes: str | None = None, limit: int = 100, offset: int = 0, request: Request = None):
    """Retorna log detalhado de uso de IA, paginado. Cada entrada: data/hora, tipo, paciente, tokens, custo."""
    owner = _owner_email(request)
    if not owner:
        raise HTTPException(status_code=401, detail=ERR_NOT_AUTHENTICATED)
    limit = max(1, min(limit, 200))
    return db.get_activity_log(owner, mes=mes, limit=limit, offset=offset)


@app.get("/agenda")
def get_agenda(mes: str | None = None, request: Request = None):
    owner = _owner_email(request)
    return db.get_agenda_owner(owner, ano_mes=mes)


@app.get("/agenda/google")
async def get_agenda_google(mes: str | None = None, request: Request = None):
    """Retorna eventos do Google Calendar primário do fisio para o mês solicitado."""
    from datetime import date, timezone, timedelta
    import calendar as _cal

    owner = _owner_email(request)
    refresh_token = db.get_google_refresh_token(owner)
    if not refresh_token:
        return {"conectado": False, "eventos": []}

    # Período do mês
    hoje = date.today()
    if mes:
        ano, m = int(mes.split("-")[0]), int(mes.split("-")[1])
    else:
        ano, m = hoje.year, hoje.month

    ultimo_dia = _cal.monthrange(ano, m)[1]
    time_min = f"{ano:04d}-{m:02d}-01T00:00:00Z"
    time_max = f"{ano:04d}-{m:02d}-{ultimo_dia:02d}T23:59:59Z"

    try:
        access_token = await calendar_service._obter_access_token(refresh_token)
    except Exception as exc:
        logger.warning("get_agenda_google: falha ao obter access_token: %s", exc)
        return {"conectado": True, "eventos": [], "erro": "Falha ao autenticar com Google Calendar"}

    try:
        async with __import__("httpx").AsyncClient() as client:
            resp = await client.get(
                GCAL_EVENTS_URL,
                params={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 250,
                },
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
        if resp.status_code != 200:
            return {"conectado": True, "eventos": [], "erro": f"Google Calendar API: {resp.status_code}"}

        items = resp.json().get("items", [])
        eventos = []
        for ev in items:
            start = ev.get("start", {})
            end   = ev.get("end", {})
            data_str = start.get("date") or (start.get("dateTime") or "")[:10]
            hora_inicio = start.get("dateTime", "")
            hora_fim    = end.get("dateTime", "")
            hora_inicio_fmt = hora_inicio[11:16] if len(hora_inicio) > 10 else ""
            hora_fim_fmt    = hora_fim[11:16]    if len(hora_fim) > 10    else ""
            eventos.append({
                "id":          ev.get("id"),
                "titulo":      ev.get("summary") or SEM_TITULO,
                "data":        data_str,
                "hora_inicio": hora_inicio_fmt,
                "hora_fim":    hora_fim_fmt,
                "dia_inteiro": "date" in start,
                "descricao":   (ev.get("description") or "")[:200],
                "color_id":    ev.get("colorId"),
                "html_link":   ev.get("htmlLink"),
            })
        return {"conectado": True, "eventos": eventos}
    except Exception as exc:
        logger.warning("get_agenda_google: exceção: %s", exc)
        return {"conectado": True, "eventos": [], "erro": str(exc)}


def _normalizar_nome(s: str) -> str:
    """Lowercase + remove acentos para comparação."""
    import unicodedata
    return ''.join(
        c for c in unicodedata.normalize('NFD', s.lower())
        if unicodedata.category(c) != 'Mn'
    )

def _buscar_paciente_por_nome(nome_evento: str, pacientes: list) -> dict:
    """
    Associa nome_evento a pacientes cadastrados com critério rigoroso + fuzzy.

    Critérios de pontuação:
      10 — nome exato (normalizado)
       8 — nome do evento contido no nome do paciente ou vice-versa
       7 — primeiro nome similar (≥0.80) E pelo menos 1 outra palavra em comum → sugestão alta
       6 — primeiro nome igual E pelo menos 1 outra palavra em comum
       5 — primeiro nome igual (sozinho)
       4 — primeiro nome similar (≥0.75) sem outras palavras em comum → sugestão média
       3 — 2+ palavras em comum (exceto stopwords), mas primeiro nome diferente
       0 — caso contrário → IGNORADO

    Resultado:
      exato    → 1 candidato com score ≥ 8 (match direto, sem pergunta)
      sugestoes → candidatos com score 4–7 (pede confirmação ao usuário)
      nenhum   → sem candidatos com pontuação suficiente
    """
    from difflib import SequenceMatcher
    stopwords = {'de', 'da', 'do', 'dos', 'das', 'e', 'a', 'o'}
    nome_n = _normalizar_nome(nome_evento)
    partes_ev = [w for w in nome_n.split() if w not in stopwords and len(w) > 1]
    if not partes_ev:
        return {"tipo": "nenhum"}

    primeiro_ev = partes_ev[0]
    scored: list[tuple[int, dict]] = []

    for p in pacientes:
        p_n = _normalizar_nome(p.get("nome", ""))
        partes_pac = [w for w in p_n.split() if w not in stopwords and len(w) > 1]
        if not partes_pac:
            continue

        if p_n == nome_n:
            score = 10
        elif nome_n in p_n or p_n in nome_n:
            score = 8
        else:
            comuns = set(partes_ev) & set(partes_pac)
            primeiro_pac = partes_pac[0]
            first_exact = (primeiro_ev == primeiro_pac)
            first_ratio = SequenceMatcher(None, primeiro_ev, primeiro_pac).ratio() if not first_exact else 1.0

            if first_exact and len(comuns) >= 2:
                score = 6
            elif first_exact:
                score = 5
            elif first_ratio >= 0.80 and len(comuns) >= 1:
                score = 7  # fuzzy nome + sobrenome em comum → sugestão alta
            elif first_ratio >= 0.75:
                score = 4  # fuzzy nome apenas → sugestão média
            elif len(comuns) >= 2:
                score = 3
            else:
                score = 0

        if score > 0:
            scored.append((score, {"id": p["id"], "nome": p["nome"]}))

    if not scored:
        return {"tipo": "nenhum"}

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][0]
    top = [s[1] for s in scored if s[0] == best]

    # Score 8+ com candidato único → exato (confirma direto)
    if best >= 8 and len(top) == 1:
        return {"tipo": "exato", "paciente": top[0]}
    # Score 6 com candidato único → exato também (primeiro nome idêntico + sobrenome)
    if best >= 5 and len(top) == 1:
        return {"tipo": "exato", "paciente": top[0]}
    # Score 4-7 ou múltiplos empatados → pede confirmação
    return {"tipo": "sugestoes", "sugestoes": top[:4]}


def _dt_br_iso(data: str, hora: str) -> str:
    """Retorna datetime ISO 8601 com offset -03:00 (Brasília)."""
    return f"{data}T{hora}:00-03:00"


async def _verificar_disponibilidade_gcal(access_token: str, data: str, hora_ini: str, hora_fim: str):
    """Verifica freebusy no Google Calendar. Retorna (disponivel: bool, busy: list)."""
    import httpx
    body = {
        "timeMin": _dt_br_iso(data, hora_ini),
        "timeMax": _dt_br_iso(data, hora_fim),
        "items":   [{"id": "primary"}],
    }
    try:
        async with httpx.AsyncClient() as c:
            resp = await c.post(
                "https://www.googleapis.com/calendar/v3/freeBusy",
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
        busy = resp.json().get("calendars", {}).get("primary", {}).get("busy", [])
        return len(busy) == 0, busy
    except Exception:
        return True, []  # fallback: assume disponível


async def _gerar_sugestoes_gcal(access_token: str, data: str, hora_ini: str, hora_fim: str) -> list:
    """Gera até 4 sugestões de horário livre (mesmo dia ±h; próximos dias mesmo horário)."""
    from datetime import datetime, timedelta

    ini_h, ini_m = int(hora_ini[:2]), int(hora_ini[3:])
    fim_h, fim_m = int(hora_fim[:2]), int(hora_fim[3:])
    dur = (fim_h * 60 + fim_m) - (ini_h * 60 + ini_m)

    candidatos = []
    # Mesmo dia: offsets de -2h, -1h, +1h, +2h
    for dh in [-2, -1, 1, 2]:
        tot = ini_h * 60 + ini_m + dh * 60
        if 7 * 60 <= tot and tot + dur <= 21 * 60:
            c_ini = f"{tot // 60:02d}:{tot % 60:02d}"
            c_fim = f"{(tot + dur) // 60:02d}:{(tot + dur) % 60:02d}"
            candidatos.append((data, c_ini, c_fim))
    # Próximos 3 dias: mesmo horário
    dt_base = datetime.fromisoformat(data)
    for dd in [1, 2, 3]:
        nd = (dt_base + timedelta(days=dd)).strftime("%Y-%m-%d")
        candidatos.append((nd, hora_ini, hora_fim))

    sugestoes = []
    for cand_data, cand_ini, cand_fim in candidatos:
        if len(sugestoes) >= 4:
            break
        ok, _ = await _verificar_disponibilidade_gcal(access_token, cand_data, cand_ini, cand_fim)
        if ok:
            sugestoes.append({"data": cand_data, "hora_inicio": cand_ini, "hora_fim": cand_fim})
    return sugestoes


_SAFE_EVENT_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,1024}$")


@app.delete("/agenda/google/{event_id}", responses={400: {"description": "Bad Request"}, 502: {"description": "Bad Gateway"}})
async def agenda_cancelar_evento(event_id: str, request: Request = None):
    """Cancela (deleta) um evento do Google Calendar primário do fisio."""
    import httpx
    if not _SAFE_EVENT_ID_RE.match(event_id):
        raise HTTPException(status_code=400, detail="event_id inválido")
    owner = _owner_email(request)
    refresh_token = db.get_google_refresh_token(owner)
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Google Calendar não conectado")
    try:
        access_token = await calendar_service._obter_access_token(refresh_token)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao autenticar: {e}")
    async with httpx.AsyncClient() as c:
        resp = await c.delete(
            f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=502, detail=f"Google Calendar: {resp.status_code}")
    return {"ok": True}


@app.get("/agenda/buscar")
async def agenda_buscar(q: str = "", request: Request = None):
    """Busca eventos por nome em sessões Physio + Google Calendar (todos os períodos)."""
    import httpx
    owner = _owner_email(request)
    q = q.strip()
    if not q:
        return {"physio": [], "gcal": []}

    # ── Physio Notes: busca em todas as sessões do owner ──
    todas = db.get_agenda_owner(owner, ano_mes=None)
    q_n = _normalizar_nome(q)
    physio = [
        s for s in todas
        if q_n in _normalizar_nome(s.get("paciente_nome") or "")
    ]

    # ── Google Calendar: busca com variações da query (insensível a acentos) ──
    gcal = []
    refresh_token = db.get_google_refresh_token(owner)
    if refresh_token:
        try:
            access_token = await calendar_service._obter_access_token(refresh_token)
            # Envia a query original E a versão sem acentos (ex: "Erica" + "erica")
            # para capturar eventos com "Érica" mesmo digitando "Erica"
            queries = list(dict.fromkeys([q, q_n]))  # mantém ordem, remove dup

            seen_ids: set = set()
            raw_items: list = []

            async with httpx.AsyncClient() as c:
                for q_term in queries:
                    resp = await c.get(
                        GCAL_EVENTS_URL,
                        params={
                            "q": q_term,
                            "singleEvents": "true",
                            "orderBy": "startTime",
                            "maxResults": 50,
                            "timeMin": "2020-01-01T00:00:00Z",
                        },
                        headers={"Authorization": f"Bearer {access_token}"},
                        timeout=10.0,
                    )
                    if resp.status_code == 200:
                        for ev in resp.json().get("items", []):
                            eid = ev.get("id")
                            if eid and eid not in seen_ids:
                                seen_ids.add(eid)
                                raw_items.append(ev)

            # Filtro adicional no servidor: aceita apenas eventos cujo título
            # contenha a query normalizada (elimina falsos positivos do Google)
            for ev in raw_items:
                titulo = ev.get("summary") or ""
                if q_n not in _normalizar_nome(titulo):
                    continue
                start = ev.get("start", {})
                end   = ev.get("end", {})
                data_str     = start.get("date") or (start.get("dateTime") or "")[:10]
                hora_inicio  = (start.get("dateTime") or "")[11:16]
                hora_fim     = (end.get("dateTime") or "")[11:16]
                gcal.append({
                    "id":          ev.get("id"),
                    "titulo":      titulo or SEM_TITULO,
                    "data":        data_str,
                    "hora_inicio": hora_inicio,
                    "hora_fim":    hora_fim,
                    "dia_inteiro": "date" in start,
                    "color_id":    ev.get("colorId"),
                })
        except Exception:
            pass

    return {"physio": physio, "gcal": gcal}


class AgendaInterpretarBody(BaseModel):
    texto: str

@app.post("/agenda/interpretar", responses={422: {"description": "Unprocessable Entity"}})
async def agenda_interpretar(body: AgendaInterpretarBody, request: Request = None):
    """Interpreta pedido de agendamento e verifica disponibilidade no Google Calendar."""
    from datetime import date
    owner = _owner_email(request)
    data_hoje = date.today().isoformat()

    try:
        parsed = await ai.interpretar_agendamento(body.texto, data_hoje, owner)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Não consegui interpretar o pedido: {e}")

    refresh_token = db.get_google_refresh_token(owner)
    if not refresh_token:
        return {"parsed": parsed, "disponivel": True, "gcal_conectado": False, "sugestoes": []}

    try:
        access_token = await calendar_service._obter_access_token(refresh_token)
    except Exception:
        return {"parsed": parsed, "disponivel": True, "gcal_conectado": True, "sugestoes": [],
                "aviso": "Não foi possível verificar o calendário"}

    disponivel, _ = await _verificar_disponibilidade_gcal(
        access_token, parsed["data"], parsed["hora_inicio"], parsed["hora_fim"]
    )
    sugestoes = []
    if not disponivel:
        sugestoes = await _gerar_sugestoes_gcal(
            access_token, parsed["data"], parsed["hora_inicio"], parsed["hora_fim"]
        )

    # Tenta associar ao paciente cadastrado
    pacientes = db.listar_pacientes(owner)
    match_pac = _buscar_paciente_por_nome(parsed.get("nome", ""), pacientes)

    return {
        "parsed":          parsed,
        "disponivel":      disponivel,
        "gcal_conectado":  True,
        "sugestoes":       sugestoes,
        "paciente_match":  match_pac.get("paciente") if match_pac["tipo"] == "exato" else None,
        "paciente_sugestoes": match_pac.get("sugestoes", []) if match_pac["tipo"] == "sugestoes" else [],
    }


class AgendaConfirmarBody(BaseModel):
    nome: str
    data: str
    hora_inicio: str
    hora_fim: str
    paciente_id: int | None = None

@app.post("/agenda/confirmar", responses={400: {"description": "Bad Request"}, 502: {"description": "Bad Gateway"}})
async def agenda_confirmar(body: AgendaConfirmarBody, request: Request = None):
    """Cria evento no Google Calendar do fisio."""
    import httpx
    owner = _owner_email(request)
    refresh_token = db.get_google_refresh_token(owner)
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Google Calendar não conectado")

    try:
        access_token = await calendar_service._obter_access_token(refresh_token)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao autenticar: {e}")

    event_body = {
        "summary": body.nome,
        "start": {"dateTime": _dt_br_iso(body.data, body.hora_inicio), "timeZone": TZ_SAO_PAULO},
        "end":   {"dateTime": _dt_br_iso(body.data, body.hora_fim),    "timeZone": TZ_SAO_PAULO},
        "colorId": "2",
    }
    async with httpx.AsyncClient() as c:
        resp = await c.post(
            GCAL_EVENTS_URL,
            json=event_body,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f"Google Calendar: {resp.status_code}")

    ev = resp.json()
    return {"ok": True, "event_id": ev.get("id")}


@app.get("/pacientes/{paciente_id}/resumo", responses={404: {"description": "Not Found"}, 502: {"description": "Bad Gateway"}})
async def resumo_paciente(paciente_id: int, tipo: str = "completo", request: Request = None):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")

    historico = db.get_historico_paciente(paciente_id)
    documentos = db.get_documentos_paciente(paciente_id)

    try:
        resumo = await ai.resumir_historico(historico, paciente, documentos, _owner_email(request), tipo=tipo)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao gerar resumo: {str(e)}")

    return {"paciente": paciente, "resumo": resumo}


@app.post("/pacientes/{paciente_id}/perguntar", responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}, 502: {"description": "Bad Gateway"}})
async def perguntar_ao_historico(paciente_id: int, body: PerguntaBody, request: Request = None):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")

    if not body.pergunta.strip():
        raise HTTPException(status_code=400, detail="Pergunta não pode ser vazia")

    historico = db.get_historico_paciente(paciente_id)

    try:
        resposta = await ai.responder_pergunta(body.pergunta, historico, paciente, _owner_email(request))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao consultar IA: {str(e)}")

    return {"pergunta": body.pergunta, "resposta": resposta}


# ---------- Documentos ----------

@app.post("/pacientes/{paciente_id}/documentos", status_code=201, responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}})
async def upload_documento(paciente_id: int, arquivo: Annotated[UploadFile, File()], request: Request = None):
    import io, uuid
    from pypdf import PdfReader

    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    if not arquivo.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Apenas arquivos PDF são aceitos")

    conteudo = await arquivo.read()

    # Extrai texto do PDF
    try:
        reader = PdfReader(io.BytesIO(conteudo))
        texto = "\n\n".join(p.extract_text() or "" for p in reader.pages).strip()
    except Exception as e:
        logger.warning("upload_documento: falha ao extrair texto PDF paciente_id=%s: %s", paciente_id, e)
        texto = ""

    # Salva arquivo em disco
    nome_arquivo = f"{uuid.uuid4().hex}.pdf"
    caminho = os.path.join(db.DOCS_DIR, nome_arquivo)
    async with aiofiles.open(caminho, "wb") as f:
        await f.write(conteudo)

    # Resumo IA (opcional — não bloqueia se falhar)
    resumo = None
    if texto:
        try:
            resumo = await ai.resumir_documento(texto, _owner_email(request))
        except Exception as e:
            logger.warning("upload_documento: falha ao gerar resumo IA paciente_id=%s: %s", paciente_id, e)

    doc = db.salvar_documento(paciente_id, arquivo.filename, nome_arquivo, resumo)
    db.registrar_audit(_owner_email(request), "documento_upload", f"id={doc['id']} paciente_id={paciente_id} nome={arquivo.filename}", _client_ip(request))
    return doc


@app.get("/pacientes/{paciente_id}/documentos", responses={404: {"description": "Not Found"}})
def listar_documentos(paciente_id: int, request: Request):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, _owner_email(request))
    return db.get_documentos_paciente(paciente_id)


@app.get("/documentos/{doc_id}/arquivo", responses={404: {"description": "Not Found"}})
def servir_documento(doc_id: int, request: Request):
    doc = db.get_documento(doc_id)
    if not doc or doc.get("deletado_em"):
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    _verificar_dono_documento(doc, _owner_email(request))
    caminho = os.path.join(db.DOCS_DIR, doc["caminho"])
    if not os.path.isfile(caminho):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no servidor")
    return FileResponse(caminho, media_type="application/pdf", filename=doc["nome_original"])


@app.delete("/documentos/{doc_id}", status_code=204, responses={404: {"description": "Not Found"}})
def deletar_documento(doc_id: int, request: Request):
    owner = _owner_email(request)
    doc = db.get_documento(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    _verificar_dono_documento(doc, owner)
    db.deletar_documento(doc_id)
    db.registrar_audit(owner, "documento_deletar", f"id={doc_id} nome={doc.get('nome_original')}", _client_ip(request))
    import os as _os
    caminho_fisico = _os.path.join(db.DOCS_DIR, doc["caminho"])
    try:
        if _os.path.isfile(caminho_fisico):
            _os.remove(caminho_fisico)
    except OSError as e:
        logger.warning("Falha ao deletar arquivo físico doc_id=%s: %s", doc_id, e)


# ---------- Pacotes ----------

@app.post("/pacientes/{paciente_id}/pacotes", status_code=201, responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}})
def criar_pacote(paciente_id: int, body: PacoteCreate, request: Request):
    owner = _owner_email(request)
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, owner)
    try:
        pacote = db.criar_pacote(paciente_id, body.total_sessoes, body.pago, body.valor_pago, body.data_pagamento, body.descricao)
        db.registrar_audit(owner, "pacote_criar", f"id={pacote['id']} paciente_id={paciente_id} sessoes={body.total_sessoes}", _client_ip(request))
        return pacote
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/pacientes/{paciente_id}/pacotes", responses={404: {"description": "Not Found"}})
def listar_pacotes(paciente_id: int, request: Request):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, _owner_email(request))
    return db.get_pacotes_paciente(paciente_id)


@app.delete("/pacotes/{pacote_id}", status_code=204)
def deletar_pacote(pacote_id: int, request: Request):
    db.deletar_pacote(pacote_id)
    db.registrar_audit(_owner_email(request), "pacote_deletar", f"id={pacote_id}", _client_ip(request))


# ---------- Notas Fiscais de Serviço (demo) ----------

@app.post("/notas-fiscais", status_code=201)
def emitir_nota_fiscal(body: NotaFiscalCreate, request: Request):
    import json as _json
    from datetime import date

    prestador_razao = body.prestador_razao or "Clínica Physio Notes Ltda"
    prestador_cnpj = body.prestador_cnpj or "00.000.000/0001-00"
    iss_aliquota = body.iss_aliquota if body.iss_aliquota is not None else 2.0
    iss_valor = round(body.valor_servico * iss_aliquota / 100, 2)
    valor_liquido = round(body.valor_servico - iss_valor, 2)

    dados = {
        "prestador": {
            "razao_social": prestador_razao,
            "cnpj": prestador_cnpj,
            "inscricao_municipal": "123456-7",
            "municipio": "São Paulo",
            "uf": "SP",
        },
        "tomador": {
            "nome": body.paciente_nome,
            "cpf": body.tomador_cpf or "000.000.000-00",
            "endereco": body.tomador_endereco or "Não informado",
        },
        "servico": {
            "descricao": body.descricao,
            "codigo_servico": "08.01",
            "iss_aliquota": iss_aliquota,
            "iss_valor": iss_valor,
            "valor_servico": body.valor_servico,
            "valor_liquido": valor_liquido,
            "competencia": body.competencia or date.today().strftime("%Y-%m"),
        },
        "observacoes": "NOTA FISCAL DEMONSTRATIVA — dados fictícios para fins de demonstração",
    }

    owner = _owner_email(request)
    nf = db.emitir_nota_fiscal(
        paciente_id=body.paciente_id,
        paciente_nome=body.paciente_nome,
        valor_servico=body.valor_servico,
        descricao=body.descricao,
        competencia=body.competencia,
        dados_json=_json.dumps(dados, ensure_ascii=False),
        owner_email=owner,
    )
    db.registrar_audit(owner, "nota_fiscal_emitir", f"id={nf['id']} valor={body.valor_servico} paciente={body.paciente_nome}", _client_ip(request))
    return {**nf, "dados": dados}


@app.get("/notas-fiscais")
def listar_notas_fiscais(
    q: str | None = None,
    paciente_id: int | None = None,
    competencia: str | None = None,
    request: Request = None,
):
    import json as _json
    owner = _owner_email(request)
    # Busca todas as notas do usuário para popular os pickers
    todas = db.listar_notas_fiscais(owner_email=owner)
    competencias_disponiveis = sorted(
        {n["competencia"] for n in todas if n.get("competencia")}, reverse=True
    )
    # Agrupa por nome (cobre notas com paciente_id=null emitidas para múltiplos)
    pac_map = {}
    for n in todas:
        nome = n.get("paciente_nome") or ""
        if not nome:
            continue
        key = n["paciente_id"] if n.get("paciente_id") else f"nome:{nome}"
        pac_map[key] = {"id": n.get("paciente_id"), "nome": nome}
    pacientes_disponiveis = sorted(pac_map.values(), key=lambda p: p["nome"])

    notas = db.listar_notas_fiscais(q=q, paciente_id=paciente_id, competencia=competencia, owner_email=owner)
    result = []
    for n in notas:
        try:
            dados = _json.loads(n.get("dados_json") or "{}")
        except Exception:
            dados = {}
        result.append({**n, "dados": dados})
    return {
        "notas": result,
        "competencias_disponiveis": competencias_disponiveis,
        "pacientes_disponiveis": pacientes_disponiveis,
    }


@app.get("/notas-fiscais/{nf_id}", responses={404: {"description": "Not Found"}})
def buscar_nota_fiscal(nf_id: int):
    import json as _json
    nf = db.get_nota_fiscal(nf_id)
    if not nf:
        raise HTTPException(status_code=404, detail="Nota fiscal não encontrada")
    try:
        dados = _json.loads(nf.get("dados_json") or "{}")
    except Exception:
        dados = {}
    return {**nf, "dados": dados}


@app.delete("/notas-fiscais/{nf_id}", status_code=204, responses={404: {"description": "Not Found"}})
def cancelar_nota_fiscal(nf_id: int, request: Request):
    nf = db.get_nota_fiscal(nf_id)
    if not nf:
        raise HTTPException(status_code=404, detail="Nota fiscal não encontrada")
    db.cancelar_nota_fiscal(nf_id)
    db.registrar_audit(_owner_email(request), "nota_fiscal_cancelar", f"id={nf_id}", _client_ip(request))


# ---------- Web Push ----------

class PushSubscribeBody(BaseModel):
    subscription: dict

@app.get("/push/vapid-public-key", responses={501: {"description": "Not Implemented"}})
async def push_vapid_key():
    if not notifications.VAPID_PUBLIC_KEY:
        raise HTTPException(status_code=501, detail="Push não configurado no servidor.")
    return {"vapid_public_key": notifications.VAPID_PUBLIC_KEY}

@app.post("/push/subscribe", responses={401: {"description": "Unauthorized"}})
async def push_subscribe(body: PushSubscribeBody, request: Request):
    owner = _owner_email(request)
    if not owner:
        raise HTTPException(status_code=401, detail="Não autenticado")
    import json
    db.salvar_subscription(owner, json.dumps(body.subscription))
    return {"ok": True}

@app.delete("/push/unsubscribe", responses={401: {"description": "Unauthorized"}})
async def push_unsubscribe(request: Request):
    owner = _owner_email(request)
    if not owner:
        raise HTTPException(status_code=401, detail="Não autenticado")
    import json
    body = await request.json()
    endpoint = body.get("endpoint", "")
    if endpoint:
        db.remover_subscription_por_endpoint(endpoint)
    return {"ok": True}


# ---------- Configurações do usuário ----------

class ConfigBody(BaseModel):
    valor_sessao_avulsa: float | None = None
    cobrar_avulsa: bool = True

@app.get("/configuracoes", responses={401: {"description": "Unauthorized"}})
async def get_configuracoes(request: Request):
    owner = _owner_email(request)
    if not owner:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return db.get_config_usuario(owner)

@app.put("/configuracoes", responses={401: {"description": "Unauthorized"}})
async def put_configuracoes(body: ConfigBody, request: Request):
    owner = _owner_email(request)
    if not owner:
        raise HTTPException(status_code=401, detail="Não autenticado")
    db.set_config_usuario(owner, body.valor_sessao_avulsa, body.cobrar_avulsa)
    return db.get_config_usuario(owner)


# ---------- Auth Google SSO ----------

class GoogleLoginBody(BaseModel):
    code: str  # authorization code do popup OAuth2 (inclui scope de Calendar)

@app.get("/auth/config")
async def auth_config():
    """Retorna o Google Client ID para o frontend inicializar o OAuth2."""
    return {
        "google_client_id": google_auth.GOOGLE_CLIENT_ID,
        "admin_email": os.environ.get("ADMIN_EMAIL", ""),
    }

@app.post("/auth/google-login", responses={401: {"description": "Unauthorized"}, 403: {"description": "Forbidden"}, 501: {"description": "Not Implemented"}})
@limiter.limit("20/minute")
async def auth_google_login(request: Request, body: GoogleLoginBody):
    """
    Recebe o authorization code do popup OAuth2, troca por tokens,
    extrai identidade do id_token, salva refresh_token e retorna JWT de sessão.
    """
    if not google_auth.GOOGLE_CLIENT_ID or not google_auth.GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=501, detail="Google SSO não configurado no servidor.")
    try:
        tokens = await google_auth.trocar_code_por_tokens(body.code)
    except Exception as e:
        db.registrar_audit(None, "login_falhou", f"troca_code={e}", _client_ip(request))
        raise HTTPException(status_code=401, detail=f"Falha ao autenticar com Google: {e}")

    id_token_str = tokens.get("id_token")
    if not id_token_str:
        raise HTTPException(status_code=401, detail="Google não retornou id_token.")

    try:
        info = google_auth.decodificar_id_token(id_token_str)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"id_token inválido: {e}")

    email = info.get("email")
    nome  = info.get("name", email)
    foto  = info.get("picture")
    if not email:
        raise HTTPException(status_code=401, detail="E-mail não disponível no token Google.")

    # Fisioterapeuta aprovado tem precedência — um e-mail só pode ter um papel no sistema.
    # Se o e-mail é fisio ATIVO (ativo=1), ignora vínculo de secretaria.
    # Fisio pendente (ativo=0) + secretaria ativa → permite login como secretaria.
    if not db.email_e_fisio_ativo(email):
        # Verifica se há convite de secretaria (ativo ou pendente) apenas para e-mails que NÃO são fisios
        status_sec = db.get_status_secretaria(email)
        if status_sec == "ativa":
            fisio_email = db.get_fisio_da_secretaria(email)
            fisio_nome = db.get_nome_fisio(fisio_email) if fisio_email else None
            token = google_auth.criar_jwt(email, nome, foto, role="secretaria", fisio_email=fisio_email, fisio_nome=fisio_nome)
            db.registrar_audit(email, "login_secretaria", f"fisio={fisio_email}", _client_ip(request))
            return {"token": token, "nome": nome, "email": email, "foto": foto, "role": "secretaria"}
        if status_sec == "pendente":
            db.registrar_audit(email, "login_secretaria_pendente", "convite aguarda aprovação", _client_ip(request))
            raise HTTPException(status_code=403, detail="Convite pendente de aprovação do administrador. Aguarde a liberação.")

    # Login normal do fisioterapeuta
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    usuario = db.upsert_usuario(email, nome, foto, admin_email)
    if not usuario.get("ativo"):
        db.registrar_audit(email, "login_negado", "acesso pendente de aprovação", _client_ip(request))
        raise HTTPException(status_code=403, detail="Acesso pendente de aprovação do administrador.")

    # Salva refresh_token (só presente no primeiro login ou quando acesso é revogado)
    refresh_token = tokens.get("refresh_token")
    if refresh_token:
        db.salvar_google_refresh_token(email, refresh_token)

    token = google_auth.criar_jwt(email, nome, foto, role="fisio")
    db.registrar_audit(email, "login", None, _client_ip(request))
    return {"token": token, "nome": nome, "email": email, "foto": foto, "role": "fisio"}


# ---------- Admin ----------

def _verificar_admin(request: Request):
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    email = _owner_email(request)
    if not email or email != admin_email:
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador.")


@app.get("/admin/usuarios")
def admin_listar_usuarios(request: Request):
    _verificar_admin(request)
    return db.listar_usuarios()


@app.post("/admin/usuarios/{email}/aprovar")
def admin_aprovar_usuario(email: str, request: Request):
    _verificar_admin(request)
    db.aprovar_usuario(email)
    db.registrar_audit(_owner_email(request), "admin_aprovar_usuario", f"target={email}", _client_ip(request))
    return {"ok": True}


@app.post("/admin/usuarios/{email}/revogar")
def admin_revogar_usuario(email: str, request: Request):
    _verificar_admin(request)
    db.revogar_usuario(email)
    db.registrar_audit(_owner_email(request), "admin_revogar_usuario", f"target={email}", _client_ip(request))
    return {"ok": True}


@app.get("/admin/audit-log")
def admin_audit_log(owner: str | None = None, limit: int = 200, request: Request = None):
    _verificar_admin(request)
    return db.get_audit_log(owner_email=owner, limit=min(limit, 1000))


@app.get("/admin/billing")
def admin_billing(mes: str | None = None, request: Request = None):
    from datetime import date
    _verificar_admin(request)
    ano_mes = mes or date.today().strftime("%Y-%m")
    return {
        "mes": ano_mes,
        "usuarios": db.get_billing_por_usuario(ano_mes),
    }


@app.get("/admin/billing/log")
def admin_billing_log(owner: str, mes: str | None = None, limit: int = 100, offset: int = 0, request: Request = None):
    """Admin consulta o log detalhado de uso de IA de uma fisio específica."""
    _verificar_admin(request)
    if not owner:
        raise HTTPException(status_code=400, detail="Parâmetro 'owner' obrigatório.")
    limit = max(1, min(limit, 200))
    return db.get_activity_log(owner, mes=mes, limit=limit, offset=offset)


@app.get("/admin/precificacao")
def admin_precificacao(cotacao: float = 5.80, request: Request = None):
    """Retorna modelo de IA, cotação USD/BRL, custo médio/usuário/mês e preço sugerido."""
    _verificar_admin(request)
    if not (1.0 <= cotacao <= 20.0):
        raise HTTPException(status_code=400, detail="Cotação fora do intervalo permitido.")

    from ai import MODEL, MODEL_PRICING
    preco_modelo = MODEL_PRICING.get(MODEL, {"input": 0.10, "output": 0.40})
    config       = db.get_config_precificacao()
    uso          = db.get_custo_medio_mensal_usd()

    custo_medio_usd = uso["custo_medio_usd"]
    custo_medio_brl = round(custo_medio_usd * cotacao, 4)

    margem  = config["margem_pct"] / 100
    imposto = config["imposto_pct"] / 100
    preco_sugerido_brl = round(custo_medio_brl * (1 + margem) * (1 + imposto), 2)

    return {
        "modelo":              MODEL,
        "preco_input_usd_1m":  preco_modelo["input"],
        "preco_output_usd_1m": preco_modelo["output"],
        "cotacao_usd_brl":     cotacao,
        "custo_medio_usd":     custo_medio_usd,
        "custo_medio_brl":     custo_medio_brl,
        "usuarios_ativos":     uso["usuarios_ativos"],
        "meses_analisados":    uso["meses_analisados"],
        "tem_dados_reais":     uso["tem_dados"],
        "margem_pct":          config["margem_pct"],
        "imposto_pct":         config["imposto_pct"],
        "preco_sugerido_brl":  preco_sugerido_brl,
    }


class PrecificacaoBody(BaseModel):
    margem_pct:  float
    imposto_pct: float

@app.post("/admin/precificacao")
def admin_precificacao_salvar(body: PrecificacaoBody, request: Request = None):
    """Salva configuração de margem e imposto para precificação."""
    _verificar_admin(request)
    if not (0 <= body.margem_pct <= 10000):
        raise HTTPException(status_code=400, detail="Margem deve estar entre 0% e 10000%.")
    if not (0 <= body.imposto_pct <= 100):
        raise HTTPException(status_code=400, detail="Imposto deve estar entre 0% e 100%.")
    return db.salvar_config_precificacao(body.margem_pct, body.imposto_pct)


# ---------- Auth WebAuthn ----------

@app.post("/auth/register/begin")
@limiter.limit("5/minute")
async def auth_register_begin(request: Request):
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        AuthenticatorAttachment,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )
    import json

    usuario = db.get_usuario_por_username(_USERNAME)
    if not usuario:
        usuario = db.criar_usuario(_USERNAME)

    options = generate_registration_options(
        rp_id=_webauthn_rp_id(request),
        rp_name="Physio Notes",
        user_id=usuario["id"].encode(),
        user_name=_USERNAME,
        user_display_name="Fisioterapeuta",
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    _challenges[_USERNAME] = options.challenge
    return json.loads(options_to_json(options))


@app.post("/auth/register/complete")
@limiter.limit("5/minute")
async def auth_register_complete(request: Request, body: dict):
    from webauthn import verify_registration_response
    from webauthn.helpers.structs import RegistrationCredential, AuthenticatorAttestationResponse
    from webauthn.helpers import base64url_to_bytes

    challenge = _challenges.get(_USERNAME)
    if not challenge:
        raise HTTPException(400, "Nenhum registro pendente. Inicie o processo novamente.")

    origin = _webauthn_origin(request)

    try:
        credential = RegistrationCredential(
            id=body["id"],
            raw_id=base64url_to_bytes(body["rawId"]),
            response=AuthenticatorAttestationResponse(
                client_data_json=base64url_to_bytes(body["response"]["clientDataJSON"]),
                attestation_object=base64url_to_bytes(body["response"]["attestationObject"]),
            ),
            type=body.get("type", "public-key"),
        )
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=_webauthn_rp_id(request),
            expected_origin=origin,
            require_user_verification=True,
        )
    except Exception as e:
        raise HTTPException(400, f"Falha na verificação: {str(e)}")

    _challenges.pop(_USERNAME, None)

    usuario = db.get_usuario_por_username(_USERNAME)
    from webauthn.helpers import bytes_to_base64url
    credential_id_str = bytes_to_base64url(verified.credential_id)
    db.salvar_credencial_webauthn(usuario["id"], credential_id_str, verified.credential_public_key, verified.sign_count)

    token = secrets.token_hex(32)
    webauthn_email = os.environ.get("WEBAUTHN_OWNER_EMAIL", "")
    _sessions[token] = webauthn_email if webauthn_email else _USERNAME
    return {"token": token, "message": "Dispositivo registrado com sucesso"}


@app.post("/auth/login/begin")
@limiter.limit("10/minute")
async def auth_login_begin(request: Request):
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement
    from webauthn.helpers import base64url_to_bytes
    import json

    usuario = db.get_usuario_por_username(_USERNAME)
    if not usuario:
        raise HTTPException(404, "Nenhum dispositivo registrado. Faça o registro primeiro.")

    credencial = db.get_credencial_webauthn(usuario["id"])
    if not credencial:
        raise HTTPException(404, "Nenhum dispositivo registrado. Faça o registro primeiro.")

    options = generate_authentication_options(
        rp_id=_webauthn_rp_id(request),
        allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(credencial["id"]))],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    _challenges[_USERNAME] = options.challenge
    return json.loads(options_to_json(options))


@app.post("/auth/login/complete")
@limiter.limit("10/minute")
async def auth_login_complete(request: Request, body: dict):
    from webauthn import verify_authentication_response
    from webauthn.helpers.structs import AuthenticationCredential, AuthenticatorAssertionResponse
    from webauthn.helpers import base64url_to_bytes

    challenge = _challenges.get(_USERNAME)
    if not challenge:
        raise HTTPException(400, "Nenhum login pendente. Inicie o processo novamente.")

    usuario = db.get_usuario_por_username(_USERNAME)
    if not usuario:
        raise HTTPException(404, "Usuário não encontrado.")

    credencial = db.get_credencial_webauthn(usuario["id"])
    if not credencial:
        raise HTTPException(404, "Credencial não encontrada.")

    origin = _webauthn_origin(request)

    try:
        credential = AuthenticationCredential(
            id=body["id"],
            raw_id=base64url_to_bytes(body["rawId"]),
            response=AuthenticatorAssertionResponse(
                client_data_json=base64url_to_bytes(body["response"]["clientDataJSON"]),
                authenticator_data=base64url_to_bytes(body["response"]["authenticatorData"]),
                signature=base64url_to_bytes(body["response"]["signature"]),
            ),
            type=body.get("type", "public-key"),
        )
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=_webauthn_rp_id(request),
            expected_origin=origin,
            credential_public_key=credencial["public_key"],
            credential_current_sign_count=credencial["sign_count"],
            require_user_verification=True,
        )
    except Exception as e:
        raise HTTPException(401, f"Autenticação falhou: {str(e)}")

    _challenges.pop(_USERNAME, None)
    db.atualizar_sign_count(credencial["id"], verified.new_sign_count)

    token = secrets.token_hex(32)
    webauthn_email = os.environ.get("WEBAUTHN_OWNER_EMAIL", "")
    _sessions[token] = webauthn_email if webauthn_email else _USERNAME
    return {"token": token}


@app.get("/auth/status")
def auth_status():
    """Informa se já existe um dispositivo registrado."""
    usuario = db.get_usuario_por_username(_USERNAME)
    if not usuario:
        return {"registered": False}
    credencial = db.get_credencial_webauthn(usuario["id"])
    return {"registered": bool(credencial)}


# ─────────────────────────────────────────────────────────────
# ATESTADO
# ─────────────────────────────────────────────────────────────

class AtestadoInterpretarBody(BaseModel):
    texto: str
    paciente_id: int

@app.post("/atestado/interpretar", responses={404: {"description": "Not Found"}, 422: {"description": "Unprocessable Entity"}})
async def atestado_interpretar(body: AtestadoInterpretarBody, request: Request = None):
    """Interpreta pedido de atestado em linguagem natural e retorna campos preenchidos pela IA."""
    from datetime import date
    owner = _owner_email(request)
    paciente = db.get_paciente(body.paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    try:
        parsed = await ai.interpretar_atestado(
            body.texto,
            date.today().isoformat(),
            paciente["nome"],
            owner,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Não consegui interpretar: {e}")
    return parsed


# ─────────────────────────────────────────────────────────────
# ADMIN — Secretaria (vínculo)
# ─────────────────────────────────────────────────────────────

class SecretariaVincularBody(BaseModel):
    secretaria_email: str

@app.post("/admin/secretaria/vincular", status_code=200, responses={401: {"description": "Unauthorized"}, 409: {"description": "Conflict"}})
def admin_vincular_secretaria(body: SecretariaVincularBody, request: Request = None):
    """Fisio convida secretaria — cria vínculo com status=pendente (aguarda aprovação do admin)."""
    fisio_email = _owner_email(request)
    if not fisio_email:
        raise HTTPException(status_code=401, detail="Não autenticado")
    if db.email_existe_como_fisio(body.secretaria_email):
        raise HTTPException(
            status_code=409,
            detail="Este e-mail já tem uma conta de fisioterapeuta. Um e-mail não pode ter dois papéis no sistema."
        )
    db.convidar_secretaria(body.secretaria_email, fisio_email)
    db.registrar_audit(fisio_email, "convidar_secretaria", body.secretaria_email, _client_ip(request))
    return {"ok": True, "secretaria_email": body.secretaria_email, "status": "pendente"}

@app.delete("/admin/secretaria/desvincular", status_code=200)
def admin_desvincular_secretaria(request: Request = None):
    """Fisio cancela convite ou remove vínculo da secretaria."""
    fisio_email = _owner_email(request)
    link = db.get_secretaria_do_fisio(fisio_email)
    if link:
        db.desvincular_secretaria(link["secretaria_email"])
        db.registrar_audit(fisio_email, "desvincular_secretaria", link["secretaria_email"], _client_ip(request))
    return {"ok": True}

@app.get("/admin/secretaria")
def admin_get_secretaria(request: Request = None):
    """Retorna a secretaria convidada/vinculada ao fisio logado (com status), ou null."""
    fisio_email = _owner_email(request)
    if not fisio_email:
        return {"secretaria": None}
    link = db.get_secretaria_do_fisio(fisio_email)
    return {"secretaria": link}

@app.get("/admin/secretaria/todas")
def admin_listar_todas_secretarias(request: Request):
    """Admin lista todos os vínculos de secretaria (pendentes e ativos)."""
    _verificar_admin(request)
    return db.listar_todos_links_secretaria()

@app.get("/admin/secretaria/pendentes")
def admin_listar_convites_pendentes(request: Request):
    """Admin lista todos os convites de secretaria aguardando aprovação."""
    _verificar_admin(request)
    return db.listar_convites_secretaria_pendentes()

@app.post("/admin/secretaria/{secretaria_email}/aprovar")
def admin_aprovar_secretaria(secretaria_email: str, request: Request):
    """Admin aprova um convite de secretaria — status passa para 'ativa'."""
    _verificar_admin(request)
    db.aprovar_secretaria(secretaria_email)
    db.registrar_audit(_owner_email(request), "admin_aprovar_secretaria", secretaria_email, _client_ip(request))
    return {"ok": True}

@app.delete("/admin/secretaria/{secretaria_email}/rejeitar")
def admin_rejeitar_secretaria(secretaria_email: str, request: Request):
    """Admin rejeita um convite de secretaria — remove o registro."""
    _verificar_admin(request)
    db.rejeitar_secretaria(secretaria_email)
    db.registrar_audit(_owner_email(request), "admin_rejeitar_secretaria", secretaria_email, _client_ip(request))
    return {"ok": True}


# ─────────────────────────────────────────────────────────────
# SECRETARIA — Endpoints (usa token com role=secretaria)
# ─────────────────────────────────────────────────────────────


@app.get("/sec/agenda")
async def sec_agenda(ano: int, mes: int, request: Request = None):
    """Retorna agenda do fisio (sessões Physio + Google Calendar)."""
    import httpx
    _, fisio_email = _sec_context(request)
    ano_mes = f"{ano}-{str(mes).zfill(2)}"

    # Sessões Physio Notes
    sessoes = db.get_agenda_owner(fisio_email, ano_mes=ano_mes)

    # Google Calendar
    gcal_eventos = []
    refresh_token = db.get_google_refresh_token(fisio_email)
    if refresh_token:
        try:
            access_token = await calendar_service._obter_access_token(refresh_token)
            from datetime import date as _date
            primeiro = f"{ano}-{str(mes).zfill(2)}-01T00:00:00-03:00"
            import calendar as _cal
            ultimo_dia = _cal.monthrange(ano, mes)[1]
            ultimo = f"{ano}-{str(mes).zfill(2)}-{ultimo_dia}T23:59:59-03:00"
            async with httpx.AsyncClient() as c:
                resp = await c.get(
                    GCAL_EVENTS_URL,
                    params={"singleEvents": "true", "orderBy": "startTime",
                            "timeMin": primeiro, "timeMax": ultimo, "maxResults": 100},
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10.0,
                )
            if resp.status_code == 200:
                for ev in resp.json().get("items", []):
                    start = ev.get("start", {})
                    end   = ev.get("end", {})
                    gcal_eventos.append({
                        "id": ev.get("id"), "titulo": ev.get("summary") or SEM_TITULO,
                        "data": start.get("date") or (start.get("dateTime") or "")[:10],
                        "hora_inicio": (start.get("dateTime") or "")[11:16],
                        "hora_fim":    (end.get("dateTime") or "")[11:16],
                        "dia_inteiro": "date" in start, "color_id": ev.get("colorId"),
                    })
        except Exception:
            pass

    return {"sessoes": sessoes, "gcal": gcal_eventos, "gcal_conectado": refresh_token is not None}


class SecAgendamentoInterpretarBody(BaseModel):
    texto: str
    data_ref: str | None = None  # data selecionada no calendário da secretaria (YYYY-MM-DD)

@app.post("/sec/agendamento/interpretar", responses={422: {"description": "Unprocessable Entity"}})
async def sec_agendamento_interpretar(body: SecAgendamentoInterpretarBody, request: Request = None):
    """IA interpreta pedido de agendamento da secretaria."""
    from datetime import date
    sec_email, fisio_email = _sec_context(request)
    refresh_token = db.get_google_refresh_token(fisio_email)
    
    try:
        # Usar fisio_email para que o billing seja atribuído ao fisio; sec_email registra quem chamou
        data_base = body.data_ref or date.today().isoformat()
        parsed = await ai.interpretar_agendamento(body.texto, data_base, fisio_email, sec_email=sec_email)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Não consegui interpretar: {e}")

    if not refresh_token:
        # Se não tem Google, apenas interpreta e retorna que não está conectado
        # O match de paciente ainda é necessário para permitir o agendamento local depois
        pacientes = db.get_pacientes(fisio_email)
        match_result = _buscar_paciente_por_nome(parsed.get("nome", ""), pacientes)
        return {
            "parsed": parsed, 
            "disponivel": True, 
            "gcal_conectado": False, 
            "sugestoes": [],
            "paciente_match": match_result.get("paciente") if match_result.get("tipo") == "exato" else None,
            "paciente_sugestoes": match_result.get("sugestoes", []) if match_result.get("tipo") == "sugestoes" else []
        }
    try:
        access_token = await calendar_service._obter_access_token(refresh_token)
        disponivel, _ = await _verificar_disponibilidade_gcal(access_token, parsed["data"], parsed["hora_inicio"], parsed["hora_fim"])
        sugestoes  = [] if disponivel else await _gerar_sugestoes_gcal(access_token, parsed["data"], parsed["hora_inicio"], parsed["hora_fim"])
    except Exception as exc:
        # Se falhar o Google (ex: token expirado no console), não trava a interpretação
        # Apenas avisa que o GCal não está funcional
        logger.warning(f"Falha ao acessar GCal para secretaria: {exc}")
        disponivel = True; sugestoes = []; refresh_token = None # Força gcal_conectado: False na resposta

    # Match de paciente
    pacientes = db.listar_pacientes(fisio_email)
    match_result = _buscar_paciente_por_nome(parsed.get("nome", ""), pacientes)
    paciente_match    = match_result.get("paciente") if match_result.get("tipo") == "exato" else None
    paciente_sugestoes = match_result.get("candidatos") if match_result.get("tipo") == "sugestoes" else []

    return {"parsed": parsed, "disponivel": disponivel, "gcal_conectado": True,
            "sugestoes": sugestoes, "paciente_match": paciente_match,
            "paciente_sugestoes": paciente_sugestoes}


class SecAgendamentoVerificarManualBody(BaseModel):
    nome: str
    data: str         # YYYY-MM-DD
    hora_inicio: str  # HH:MM
    hora_fim: str     # HH:MM

@app.post("/sec/agendamento/verificar-manual", responses={422: {"description": "Unprocessable Entity"}})
async def sec_agendamento_verificar_manual(body: SecAgendamentoVerificarManualBody, request: Request = None):
    """Verifica disponibilidade sem IA: patient matching + freebusy. Mesmo shape de resposta que /interpretar."""
    sec_email, fisio_email = _sec_context(request)
    refresh_token = db.get_google_refresh_token(fisio_email)

    parsed = {"nome": body.nome, "data": body.data, "hora_inicio": body.hora_inicio, "hora_fim": body.hora_fim}
    pacientes = db.listar_pacientes(fisio_email)
    match_result = _buscar_paciente_por_nome(body.nome, pacientes)
    paciente_match     = match_result.get("paciente") if match_result.get("tipo") == "exato" else None
    paciente_sugestoes = match_result.get("sugestoes", []) if match_result.get("tipo") == "sugestoes" else []

    if not refresh_token:
        return {"parsed": parsed, "disponivel": True, "gcal_conectado": False,
                "sugestoes": [], "paciente_match": paciente_match, "paciente_sugestoes": paciente_sugestoes}

    try:
        access_token = await calendar_service._obter_access_token(refresh_token)
        disponivel, _ = await _verificar_disponibilidade_gcal(
            access_token, body.data, body.hora_inicio, body.hora_fim
        )
        sugestoes = [] if disponivel else await _gerar_sugestoes_gcal(
            access_token, body.data, body.hora_inicio, body.hora_fim
        )
    except Exception as exc:
        logger.warning(f"Falha ao acessar GCal no verificar-manual: {exc}")
        disponivel = True; sugestoes = []; refresh_token = None

    return {"parsed": parsed, "disponivel": disponivel, "gcal_conectado": refresh_token is not None,
            "sugestoes": sugestoes, "paciente_match": paciente_match, "paciente_sugestoes": paciente_sugestoes}


class SecAgendamentoConfirmarBody(BaseModel):
    nome: str
    data: str
    hora_inicio: str
    hora_fim: str
    paciente_id: int | None = None
    forcar: bool = False  # True = confirmar mesmo com horário ocupado

class SecPacotePagamentoBody(BaseModel):
    pago: bool

@app.post("/sec/agendamento/confirmar", responses={400: {"description": "Bad Request"}, 409: {"description": "Conflict"}, 502: {"description": "Bad Gateway"}})
async def sec_agendamento_confirmar(body: SecAgendamentoConfirmarBody, request: Request = None):
    """Cria evento no Google Calendar do fisio (ou local se não conectado)."""
    import httpx
    _, fisio_email = _sec_context(request)
    refresh_token = db.get_google_refresh_token(fisio_email)

    if not refresh_token:
        # Fallback: Agendamento local se não houver Google Calendar
        if body.paciente_id:
            db.criar_sessao(body.paciente_id, body.data)
            db.registrar_audit(fisio_email, "agendamento_local_sec", f"paciente_id={body.paciente_id} data={body.data}", _client_ip(request))
            return {"ok": True, "gcal": False, "local": True}
        raise HTTPException(status_code=400, detail="Google Calendar não conectado. Para agendar localmente no sistema, identifique o paciente.")

    try:
        access_token = await calendar_service._obter_access_token(refresh_token)
    except Exception:
        if body.paciente_id:
            db.criar_sessao(body.paciente_id, body.data)
            db.registrar_audit(fisio_email, "agendamento_local_sec_fallback", f"paciente_id={body.paciente_id} data={body.data}", _client_ip(request))
            return {"ok": True, "gcal": False, "local": True, "info": "Google desconectado, salvo localmente."}
        raise HTTPException(status_code=400, detail="A conexão com o Google da fisioterapeuta expirou. Ela precisa reconectar.")

    # Verificação de conflito no momento da confirmação (não só no interpretar)
    if not body.forcar:
        try:
            slot_livre, _ = await _verificar_disponibilidade_gcal(
                access_token, body.data, body.hora_inicio, body.hora_fim
            )
            if not slot_livre:
                raise HTTPException(
                    status_code=409,
                    detail="Horário já está ocupado na agenda. Envie forcar=true para confirmar mesmo assim."
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning(f"Falha ao verificar freebusy no confirmar: {exc}")
            # Se não conseguir checar, deixa prosseguir

    nome_evento = body.nome
    if body.paciente_id:
        pac = db.get_paciente(body.paciente_id)
        if pac:
            _verificar_dono(pac, fisio_email)
            nome_evento = pac["nome"]
    event = {
        "summary": f"Physio — {nome_evento}",
        "start": {"dateTime": _dt_br_iso(body.data, body.hora_inicio), "timeZone": TZ_SAO_PAULO},
        "end":   {"dateTime": _dt_br_iso(body.data, body.hora_fim),    "timeZone": TZ_SAO_PAULO},
    }
    async with httpx.AsyncClient() as c:
        resp = await c.post(
            GCAL_EVENTS_URL,
            json=event, headers={"Authorization": f"Bearer {access_token}"}, timeout=10.0,
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f"Google Calendar: {resp.status_code}")
    return {"ok": True, "event_id": resp.json().get("id")}


@app.delete("/sec/agendamento/{event_id}", responses={400: {"description": "Bad Request"}, 502: {"description": "Bad Gateway"}})
async def sec_agendamento_cancelar(event_id: str, request: Request = None):
    """Cancela evento do Google Calendar do fisio."""
    import httpx
    if not _SAFE_EVENT_ID_RE.match(event_id):
        raise HTTPException(status_code=400, detail="event_id inválido")
    _, fisio_email = _sec_context(request)
    refresh_token = db.get_google_refresh_token(fisio_email)
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Google Calendar do fisio não conectado")
    
    try:
        access_token = await calendar_service._obter_access_token(refresh_token)
    except Exception:
        raise HTTPException(status_code=400, detail="Não foi possível acessar o Google Calendar da fisioterapeuta (Conexão expirada).")

    async with httpx.AsyncClient() as c:
        resp = await c.delete(
            f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"}, timeout=10.0,
        )
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=502, detail=f"Google Calendar: {resp.status_code}")
    return {"ok": True}


class SecAtestadoInterpretarBody(BaseModel):
    texto: str
    paciente_id: int | None = None

@app.post("/sec/atestado/interpretar", responses={422: {"description": "Unprocessable Entity"}})
async def sec_atestado_interpretar(body: SecAtestadoInterpretarBody, request: Request = None):
    """IA interpreta pedido de atestado feito pela secretaria."""
    from datetime import date
    sec_email, fisio_email = _sec_context(request)
    paciente_nome = ""
    if body.paciente_id:
        pac = db.get_paciente(body.paciente_id)
        if pac:
            _verificar_dono(pac, fisio_email)
            paciente_nome = pac["nome"]
    try:
        parsed = await ai.interpretar_atestado(body.texto, date.today().isoformat(), paciente_nome, fisio_email, sec_email=sec_email)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Não consegui interpretar: {e}")
    return parsed


# ---------- Secretaria — Pacientes ----------

@app.get("/sec/pacientes")
def sec_listar_pacientes(request: Request = None):
    """Secretaria lista os pacientes do fisio vinculado."""
    _, fisio_email = _sec_context(request)
    return db.listar_pacientes(fisio_email)


@app.post("/sec/pacientes", status_code=201, responses={409: {"description": "Conflict"}})
def sec_criar_paciente(body: PacienteCreate, request: Request = None):
    """Secretaria cadastra novo paciente no nome do fisio vinculado."""
    _, fisio_email = _sec_context(request)
    try:
        paciente = db.criar_paciente(
            body.nome, body.data_nascimento, body.observacoes,
            body.anamnese, body.cpf, body.endereco, fisio_email,
        )
    except (sqlite3.IntegrityError, ValueError):
        raise HTTPException(status_code=409, detail="Paciente com este CPF já cadastrado.")
    db.registrar_audit(fisio_email, "sec_paciente_criar", f"id={paciente['id']} nome={paciente['nome']}", _client_ip(request))
    return paciente


@app.put("/sec/pacientes/{paciente_id}", responses={404: {"description": "Not Found"}, 409: {"description": "Conflict"}})
def sec_atualizar_paciente(paciente_id: int, body: PacienteUpdate, request: Request = None):
    """Secretaria atualiza dados cadastrais de um paciente do fisio vinculado."""
    sec_email, fisio_email = _sec_context(request)
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    
    _verificar_dono(paciente, fisio_email)

    try:
        # A secretaria pode atualizar nome, nascimento, observacoes, cpf e endereco.
        # Campos clínicos (anamnese, conduta) são omitidos/ignorados no frontend da sec, 
        # mas mantemos a chamada ao db.atualizar_paciente consistente.
        resultado = db.atualizar_paciente(
            paciente_id,
            body.nome,
            body.data_nascimento,
            body.anamnese or paciente.get("anamnese"),
            body.cpf,
            body.endereco,
            body.conduta_tratamento or paciente.get("conduta_tratamento")
        )
        # Observações (campo novo solicitado)
        if body.observacoes is not None:
             with db.get_conn() as conn:
                 conn.execute("UPDATE paciente SET observacoes = ? WHERE id = ?", (body.observacoes, paciente_id))
                 conn.commit()
                 resultado["observacoes"] = body.observacoes

    except (sqlite3.IntegrityError, ValueError):
        raise HTTPException(status_code=409, detail="Paciente com este CPF já cadastrado.")
    
    db.registrar_audit(fisio_email, "sec_paciente_atualizar", f"id={paciente_id} por={sec_email}", _client_ip(request))
    return resultado


# ---------- Secretaria — Pacotes ----------

@app.get("/sec/pacientes/{paciente_id}/pacotes", responses={404: {"description": "Not Found"}})
def sec_listar_pacotes(paciente_id: int, request: Request = None):
    """Secretaria lista os pacotes de um paciente do fisio vinculado."""
    _, fisio_email = _sec_context(request)
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, fisio_email)
    return db.get_pacotes_paciente(paciente_id)


@app.post("/sec/pacientes/{paciente_id}/pacotes", status_code=201, responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}})
def sec_criar_pacote(paciente_id: int, body: PacoteCreate, request: Request = None):
    """Secretaria cria pacote de sessões para um paciente do fisio vinculado."""
    _, fisio_email = _sec_context(request)
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, fisio_email)
    try:
        pacote = db.criar_pacote(paciente_id, body.total_sessoes, body.pago, body.valor_pago, body.data_pagamento, body.descricao)
        db.registrar_audit(fisio_email, "sec_pacote_criar", f"id={pacote['id']} paciente_id={paciente_id} sessoes={body.total_sessoes}", _client_ip(request))
        return pacote
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/sec/pacotes/ativos")
def sec_listar_pacotes_ativos(request: Request = None):
    """Secretaria lista todos os pacotes ativos do fisioterapeuta."""
    _, fisio_email = _sec_context(request)
    return db.listar_todos_pacotes_ativos(fisio_email)


@app.patch("/sec/pacotes/{pacote_id}/pagamento", responses={404: {"description": "Not Found"}})
def sec_atualizar_pagamento_pacote(pacote_id: int, body: SecPacotePagamentoBody, request: Request = None):
    """Secretaria atualiza o status de pagamento de um pacote."""
    sec_email, fisio_email = _sec_context(request)
    # Verificar se o pacote pertence a um paciente do fisio
    with db.get_conn() as conn:
        row = conn.execute("""
            SELECT pk.id, p.nome 
            FROM pacote pk 
            JOIN paciente p ON p.id = pk.paciente_id 
            WHERE pk.id = ? AND p.owner_email = ?
        """, (pacote_id, fisio_email)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Pacote não encontrado")
    
    db.atualizar_pagamento_pacote(pacote_id, body.pago)
    db.registrar_audit(fisio_email, "sec_pacote_pagamento", f"id={pacote_id} pago={body.pago} por={sec_email}", _client_ip(request))
    return {"ok": True}


# ---------- Frontend (deve ser montado por último) ----------

_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")

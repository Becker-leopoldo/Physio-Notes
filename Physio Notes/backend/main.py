import logging
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import database as db
import transcribe
import ai
import google_auth
import notifications

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("physio_notes")

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
_PREFIXOS_PUBLICOS = ("/login", "/admin", "/manifest", "/sw.", "/icon", "/favicon", "/.well-known")

@app.middleware("http")
async def verificar_autenticacao(request: Request, call_next):
    path = request.url.path
    # Arquivos estáticos e rotas de auth: livres
    if (path in _ROTAS_PUBLICAS
            or any(path.startswith(p) for p in _PREFIXOS_PUBLICOS)
            or path in ("/", "/index.html")):
        return await call_next(request)

    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        # Aceita JWT do Google SSO
        try:
            google_auth.verificar_jwt(token)
            return await call_next(request)
        except Exception as e:
            logger.info("verificar_autenticacao: JWT inválido path=%s: %s", path, e)
        # Aceita sessão WebAuthn (compatibilidade com usuário existente)
        if token in _sessions:
            return await call_next(request)

    return JSONResponse(status_code=401, content={"detail": "Não autenticado"})


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
    ff = request.headers.get("x-forwarded-for")
    if ff:
        return ff.split(",")[0].strip()
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


def _verificar_dono(paciente: dict, owner: str | None):
    """Lança 404 se o paciente não pertence ao usuário (None = WebAuthn, vê tudo)."""
    if owner and paciente.get("owner_email") and paciente["owner_email"] != owner:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")


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

@app.post("/pacientes", status_code=201)
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


@app.get("/pacientes/{paciente_id}")
def buscar_paciente(paciente_id: int, request: Request):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, _owner_email(request))
    return paciente


class PacienteUpdate(BaseModel):
    nome: str
    data_nascimento: str | None = None
    anamnese: str | None = None
    cpf: str | None = None
    endereco: str | None = None
    conduta_tratamento: str | None = None


@app.delete("/pacientes/{paciente_id}", status_code=204)
def deletar_paciente(paciente_id: int, request: Request):
    owner = _owner_email(request)
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, owner)
    db.deletar_paciente(paciente_id)
    db.registrar_audit(owner, "paciente_deletar", f"id={paciente_id} nome={paciente['nome']}", _client_ip(request))


@app.put("/pacientes/{paciente_id}")
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


@app.post("/pacientes/{paciente_id}/complementar-anamnese")
async def complementar_anamnese(paciente_id: int, body: ComplementarAnamneseBody, request: Request):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, _owner_email(request))
    anamnese_atualizada = await ai.complementar_anamnese(body.transcricao, paciente.get("anamnese"), _owner_email(request))
    paciente_atualizado = db.atualizar_paciente(
        paciente_id, paciente["nome"], paciente.get("data_nascimento"),
        anamnese_atualizada, paciente.get("cpf"), paciente.get("endereco"),
        paciente.get("conduta_tratamento"),
    )
    return {"anamnese": paciente_atualizado["anamnese"]}


class ComplementarCondutaBody(BaseModel):
    transcricao: str


@app.post("/pacientes/{paciente_id}/complementar-conduta")
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


@app.post("/pacientes/{paciente_id}/sugerir-conduta")
async def sugerir_conduta(paciente_id: int, request: Request):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, _owner_email(request))
    if not paciente.get("anamnese"):
        raise HTTPException(status_code=400, detail="Paciente não possui anamnese registrada. Registre a anamnese primeiro.")
    sugestao = await ai.sugerir_conduta(paciente["anamnese"], _owner_email(request))
    return {"sugestao": sugestao}


@app.post("/transcrever")
@limiter.limit("20/minute")
async def transcrever_audio_avulso(request: Request, audio: UploadFile = File(...)):
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Arquivo de áudio vazio")
    try:
        transcricao = await transcribe.transcrever_audio(audio_bytes, audio.filename or "audio.webm")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na transcrição: {str(e)}")
    return {"transcricao": transcricao}


@app.post("/extrair-paciente")
async def extrair_dados_paciente(body: ExtrairPacienteBody, request: Request):
    if not body.transcricao.strip():
        raise HTTPException(status_code=400, detail="Transcrição vazia")
    try:
        dados = await ai.extrair_dados_paciente(body.transcricao, _owner_email(request))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na extração: {str(e)}")
    return dados


@app.post("/extrair-procedimento")
async def extrair_procedimento(body: ExtrairProcedimentoBody, request: Request):
    if not body.transcricao.strip():
        raise HTTPException(status_code=400, detail="Transcrição vazia")
    try:
        dados = await ai.extrair_procedimento(body.transcricao, _owner_email(request))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na extração: {str(e)}")
    return dados


@app.post("/sessoes/{sessao_id}/detectar-procedimentos")
async def detectar_procedimentos(sessao_id: int, request: Request):
    """
    Analisa transcrição + nota da sessão com IA e retorna sugestões de
    procedimentos extras detectados. NÃO salva automaticamente — retorna
    as sugestões para o frontend confirmar.
    """
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
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


@app.get("/sessoes/{sessao_id}/procedimentos")
def listar_procedimentos(sessao_id: int, request: Request):
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    _verificar_dono_sessao(sessao, _owner_email(request))
    return db.get_procedimentos_sessao(sessao_id)


@app.post("/sessoes/{sessao_id}/procedimentos")
def criar_procedimento(sessao_id: int, body: ProcedimentoCreate, request: Request):
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
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


@app.post("/extrair-pacote")
async def extrair_dados_pacote(body: ExtrairPacoteBody, request: Request):
    if not body.transcricao.strip():
        raise HTTPException(status_code=400, detail="Transcrição vazia")
    try:
        dados = await ai.extrair_dados_pacote(body.transcricao, _owner_email(request))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na extração: {str(e)}")
    return dados



# ---------- Sessoes ----------

@app.post("/sessoes", status_code=201)
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


@app.get("/sessoes/{sessao_id}")
def buscar_sessao(sessao_id: int, request: Request):
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    _verificar_dono_sessao(sessao, _owner_email(request))

    chunks = db.get_chunks_sessao(sessao_id)
    consolidado = db.get_consolidado_sessao(sessao_id)

    return {**sessao, "chunks": chunks, "consolidado": consolidado}


@app.post("/sessoes/{sessao_id}/audio")
async def upload_audio(sessao_id: int, audio: UploadFile = File(...), request: Request = None):
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    _verificar_dono_sessao(sessao, _owner_email(request))
    if sessao["status"] != "aberta":
        raise HTTPException(status_code=400, detail="Sessão já encerrada")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Arquivo de áudio vazio")

    try:
        transcricao = await transcribe.transcrever_audio(audio_bytes, audio.filename or "audio.webm")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na transcrição: {str(e)}")

    chunk = db.add_audio_chunk(sessao_id, transcricao)
    return {"chunk": chunk, "transcricao": transcricao}


class CancelamentoBody(BaseModel):
    cobrar: bool = True
    valor: float | None = None
    complemento: str | None = None


@app.post("/sessoes/{sessao_id}/cancelar-com-cobranca")
def cancelar_com_cobranca(sessao_id: int, body: CancelamentoBody, request: Request):
    owner = _owner_email(request)
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    _verificar_dono_sessao(sessao, owner)
    if sessao["status"] != "aberta":
        raise HTTPException(status_code=400, detail="Sessão não está aberta")
    db.registrar_cancelamento(sessao_id, body.cobrar, body.valor, body.complemento, owner)
    db.registrar_audit(owner, "sessao_cancelar", f"id={sessao_id} cobrar={body.cobrar}", _client_ip(request))
    return {"status": "cancelada", "sessao_id": sessao_id}


@app.delete("/sessoes/{sessao_id}")
def cancelar_sessao(sessao_id: int, request: Request):
    owner = _owner_email(request)
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
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


@app.post("/sessoes/{sessao_id}/adicionar-audio")
async def adicionar_audio_sessao_encerrada(sessao_id: int, audio: UploadFile = File(...), request: Request = None):
    """Adiciona áudio a uma sessão encerrada do mesmo dia, sem abater do pacote."""
    from datetime import date
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    _verificar_dono_sessao(sessao, _owner_email(request))
    if sessao["status"] == "aberta":
        raise HTTPException(status_code=400, detail="Use o endpoint /audio para sessões abertas")
    if sessao["data"] != date.today().isoformat():
        raise HTTPException(status_code=400, detail="Só é possível adicionar notas em sessões do dia atual")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Arquivo de áudio vazio")

    try:
        transcricao = await transcribe.transcrever_audio(audio_bytes, audio.filename or "audio.webm")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na transcrição: {str(e)}")

    chunk = db.add_audio_chunk(sessao_id, transcricao)

    # Re-consolida com todos os chunks (sem deducao de pacote)
    chunks = db.get_chunks_sessao(sessao_id)
    try:
        dados = await ai.consolidar_sessao([c["transcricao"] for c in chunks], _owner_email(request))
        db.salvar_consolidado(sessao_id, dados)
    except Exception as e:
        logger.warning("adicionar_audio: falha ao re-consolidar sessao_id=%s: %s", sessao_id, e)

    return {"chunk": chunk, "transcricao": transcricao}


class EncerrarBody(BaseModel):
    cobrar: bool = True
    valor: float | None = None


@app.post("/sessoes/{sessao_id}/encerrar")
@limiter.limit("10/minute")
async def encerrar_sessao(sessao_id: int, body: EncerrarBody = None, request: Request = None):
    if body is None:
        body = EncerrarBody()
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
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

    try:
        dados_consolidados = await ai.consolidar_sessao(transcricoes, owner)
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
    return {
        "consolidado": consolidado,
        "sessao_id": sessao_id,
        "status": "encerrada",
        "sessao_avulsa_valor": resultado_encerramento.get("sessao_avulsa_valor"),
        "valor_ai_detectado": valor_ai_detectado,
        "cobrar": body.cobrar,
    }


@app.get("/pacientes/{paciente_id}/sessoes")
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


@app.get("/billing")
def billing(mes: str | None = None, request: Request = None):
    from datetime import date
    owner = _owner_email(request)
    ano_mes = mes or date.today().strftime("%Y-%m")
    return {
        "mes_atual": db.get_billing_mes(ano_mes, owner),
        "historico": db.get_billing_meses(owner),
    }


@app.get("/pacientes/{paciente_id}/resumo")
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


@app.post("/pacientes/{paciente_id}/perguntar")
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

@app.post("/pacientes/{paciente_id}/documentos", status_code=201)
async def upload_documento(paciente_id: int, arquivo: UploadFile = File(...), request: Request = None):
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
    with open(caminho, "wb") as f:
        f.write(conteudo)

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


@app.get("/pacientes/{paciente_id}/documentos")
def listar_documentos(paciente_id: int, request: Request):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, _owner_email(request))
    return db.get_documentos_paciente(paciente_id)


@app.get("/documentos/{doc_id}/arquivo")
def servir_documento(doc_id: int, request: Request):
    doc = db.get_documento(doc_id)
    if not doc or doc.get("deletado_em"):
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    _verificar_dono_documento(doc, _owner_email(request))
    caminho = os.path.join(db.DOCS_DIR, doc["caminho"])
    if not os.path.isfile(caminho):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no servidor")
    return FileResponse(caminho, media_type="application/pdf", filename=doc["nome_original"])


@app.delete("/documentos/{doc_id}", status_code=204)
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

@app.post("/pacientes/{paciente_id}/pacotes", status_code=201)
def criar_pacote(paciente_id: int, body: PacoteCreate, request: Request):
    owner = _owner_email(request)
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, owner)
    pacote = db.criar_pacote(paciente_id, body.total_sessoes, body.valor_pago, body.data_pagamento, body.descricao)
    db.registrar_audit(owner, "pacote_criar", f"id={pacote['id']} paciente_id={paciente_id} sessoes={body.total_sessoes}", _client_ip(request))
    return pacote


@app.get("/pacientes/{paciente_id}/pacotes")
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


@app.get("/notas-fiscais/{nf_id}")
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


@app.delete("/notas-fiscais/{nf_id}", status_code=204)
def cancelar_nota_fiscal(nf_id: int, request: Request):
    nf = db.get_nota_fiscal(nf_id)
    if not nf:
        raise HTTPException(status_code=404, detail="Nota fiscal não encontrada")
    db.cancelar_nota_fiscal(nf_id)
    db.registrar_audit(_owner_email(request), "nota_fiscal_cancelar", f"id={nf_id}", _client_ip(request))


# ---------- Web Push ----------

class PushSubscribeBody(BaseModel):
    subscription: dict

@app.get("/push/vapid-public-key")
async def push_vapid_key():
    if not notifications.VAPID_PUBLIC_KEY:
        raise HTTPException(status_code=501, detail="Push não configurado no servidor.")
    return {"vapid_public_key": notifications.VAPID_PUBLIC_KEY}

@app.post("/push/subscribe")
async def push_subscribe(body: PushSubscribeBody, request: Request):
    owner = _owner_email(request)
    if not owner:
        raise HTTPException(status_code=401, detail="Não autenticado")
    import json
    db.salvar_subscription(owner, json.dumps(body.subscription))
    return {"ok": True}

@app.delete("/push/unsubscribe")
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

@app.get("/configuracoes")
async def get_configuracoes(request: Request):
    owner = _owner_email(request)
    if not owner:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return db.get_config_usuario(owner)

@app.put("/configuracoes")
async def put_configuracoes(body: ConfigBody, request: Request):
    owner = _owner_email(request)
    if not owner:
        raise HTTPException(status_code=401, detail="Não autenticado")
    db.set_config_usuario(owner, body.valor_sessao_avulsa, body.cobrar_avulsa)
    return db.get_config_usuario(owner)


# ---------- Auth Google SSO ----------

class GoogleLoginBody(BaseModel):
    credential: str

@app.get("/auth/config")
async def auth_config():
    """Retorna o Google Client ID para o frontend inicializar o GIS."""
    return {
        "google_client_id": google_auth.GOOGLE_CLIENT_ID,
        "admin_email": os.environ.get("ADMIN_EMAIL", ""),
    }

@app.post("/auth/google-login")
@limiter.limit("20/minute")
async def auth_google_login(request: Request, body: GoogleLoginBody):
    """Verifica o credential do Google, cria/atualiza usuário e retorna JWT."""
    if not google_auth.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google SSO não configurado no servidor.")
    try:
        info = google_auth.verificar_google_token(body.credential)
    except Exception as e:
        db.registrar_audit(None, "login_falhou", f"erro={e}", _client_ip(request))
        raise HTTPException(status_code=401, detail=f"Token Google inválido: {e}")
    email = info.get("email")
    nome  = info.get("name", email)
    foto  = info.get("picture")
    if not email:
        raise HTTPException(status_code=401, detail="E-mail não disponível no token Google.")
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    usuario = db.upsert_usuario(email, nome, foto, admin_email)
    if not usuario.get("ativo"):
        db.registrar_audit(email, "login_negado", "acesso pendente de aprovação", _client_ip(request))
        raise HTTPException(status_code=403, detail="Acesso pendente de aprovação do administrador.")
    token = google_auth.criar_jwt(email, nome, foto)
    db.registrar_audit(email, "login", None, _client_ip(request))
    return {"token": token, "nome": nome, "email": email, "foto": foto}


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


# ---------- Frontend (deve ser montado por último) ----------

_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")

import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import database as db
import transcribe
import ai
import google_auth

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
    yield


app = FastAPI(title="Physio Notes API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Auth middleware ----------
# Rotas que NÃO precisam de token (auth + arquivos estáticos)
_ROTAS_PUBLICAS = {
    "/auth/config", "/auth/google-login",
    "/auth/register/begin", "/auth/register/complete",
    "/auth/login/begin", "/auth/login/complete", "/auth/status",
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
        except Exception:
            pass
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


class RelatorioCREFITOBody(BaseModel):
    paciente_ids: list[int]


class PacoteCreate(BaseModel):
    total_sessoes: int
    valor_pago: float | None = None
    data_pagamento: str | None = None
    descricao: str | None = None


class ProcedimentoCreate(BaseModel):
    descricao: str
    valor: float | None = None
    data: str | None = None


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

def _owner_email(request: Request) -> str | None:
    """Extrai email do JWT. Retorna None para sessões WebAuthn (legado)."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1]
        try:
            payload = google_auth.verificar_jwt(token)
            return payload.get("sub")
        except Exception:
            pass
    return None


def _verificar_dono(paciente: dict, owner: str | None):
    """Lança 404 se o paciente não pertence ao usuário (None = WebAuthn, vê tudo)."""
    if owner and paciente.get("owner_email") and paciente["owner_email"] != owner:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")


# ---------- Pacientes ----------

@app.post("/pacientes", status_code=201)
def criar_paciente(body: PacienteCreate, request: Request):
    owner = _owner_email(request)
    paciente = db.criar_paciente(body.nome, body.data_nascimento, body.observacoes, body.anamnese, body.cpf, body.endereco, owner)
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


@app.delete("/pacientes/{paciente_id}", status_code=204)
def deletar_paciente(paciente_id: int, request: Request):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, _owner_email(request))
    db.deletar_paciente(paciente_id)


@app.put("/pacientes/{paciente_id}")
def atualizar_paciente(paciente_id: int, body: PacienteUpdate, request: Request):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, _owner_email(request))
    return db.atualizar_paciente(paciente_id, body.nome, body.data_nascimento, body.anamnese, body.cpf, body.endereco)


@app.post("/pacientes/{paciente_id}/complementar-anamnese")
async def complementar_anamnese(paciente_id: int, body: ComplementarAnamneseBody, request: Request):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _verificar_dono(paciente, _owner_email(request))
    anamnese_atualizada = await ai.complementar_anamnese(body.transcricao, paciente.get("anamnese"))
    paciente_atualizado = db.atualizar_paciente(
        paciente_id,
        paciente["nome"],
        paciente.get("data_nascimento"),
        anamnese_atualizada,
        paciente.get("cpf"),
        paciente.get("endereco"),
    )
    return {"anamnese": paciente_atualizado["anamnese"]}


@app.post("/transcrever")
async def transcrever_audio_avulso(audio: UploadFile = File(...)):
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Arquivo de áudio vazio")
    try:
        transcricao = await transcribe.transcrever_audio(audio_bytes, audio.filename or "audio.webm")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na transcrição: {str(e)}")
    return {"transcricao": transcricao}


@app.post("/extrair-paciente")
async def extrair_dados_paciente(body: ExtrairPacienteBody):
    if not body.transcricao.strip():
        raise HTTPException(status_code=400, detail="Transcrição vazia")
    try:
        dados = await ai.extrair_dados_paciente(body.transcricao)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na extração: {str(e)}")
    return dados


@app.post("/extrair-procedimento")
async def extrair_procedimento(body: ExtrairProcedimentoBody):
    if not body.transcricao.strip():
        raise HTTPException(status_code=400, detail="Transcrição vazia")
    try:
        dados = await ai.extrair_procedimento(body.transcricao)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na extração: {str(e)}")
    return dados


@app.post("/sessoes/{sessao_id}/detectar-procedimentos")
async def detectar_procedimentos(sessao_id: int):
    """
    Analisa transcrição + nota da sessão com IA e retorna sugestões de
    procedimentos extras detectados. NÃO salva automaticamente — retorna
    as sugestões para o frontend confirmar.
    """
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    chunks = db.get_chunks_sessao(sessao_id)
    consolidado = db.get_consolidado_sessao(sessao_id)

    transcricao = "\n".join(c["transcricao"] for c in chunks if c.get("transcricao"))
    if not transcricao.strip():
        return {"sugestoes": []}

    nota = consolidado.get("nota") or consolidado.get("conduta") if consolidado else None

    try:
        sugestoes = await ai.detectar_procedimentos_extras(transcricao, nota)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na IA: {str(e)}")

    return {"sugestoes": sugestoes}


@app.get("/sessoes/{sessao_id}/procedimentos")
def listar_procedimentos(sessao_id: int):
    return db.get_procedimentos_sessao(sessao_id)


@app.post("/sessoes/{sessao_id}/procedimentos")
def criar_procedimento(sessao_id: int, body: ProcedimentoCreate):
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return db.adicionar_procedimento(sessao_id, sessao["paciente_id"], body.descricao, body.valor, body.data)


@app.delete("/procedimentos/{proc_id}")
def deletar_procedimento(proc_id: int):
    db.deletar_procedimento(proc_id)
    return {"ok": True}


@app.post("/extrair-pacote")
async def extrair_dados_pacote(body: ExtrairPacoteBody):
    if not body.transcricao.strip():
        raise HTTPException(status_code=400, detail="Transcrição vazia")
    try:
        dados = await ai.extrair_dados_pacote(body.transcricao)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro na extração: {str(e)}")
    return dados


# ---------- Relatorio CREFITO ----------

@app.post("/relatorio/crefito")
async def relatorio_crefito(body: RelatorioCREFITOBody):
    resultado = []
    for paciente_id in body.paciente_ids:
        paciente = db.get_paciente(paciente_id)
        if not paciente:
            continue

        historico = db.get_historico_paciente(paciente_id)

        if historico:
            try:
                resumo = await ai.resumir_historico(historico, paciente)
            except Exception:
                resumo = None
        else:
            resumo = None

        sessoes_encerradas = [s for s in historico if s.get("status") == "encerrada"]
        total_sessoes = len(sessoes_encerradas)

        datas = [s.get("data") for s in sessoes_encerradas if s.get("data")]
        datas_sorted = sorted(datas) if datas else []
        primeira_sessao = datas_sorted[0] if datas_sorted else None
        ultima_sessao = datas_sorted[-1] if datas_sorted else None

        resultado.append({
            "paciente": paciente,
            "resumo": resumo,
            "total_sessoes": total_sessoes,
            "primeira_sessao": primeira_sessao,
            "ultima_sessao": ultima_sessao,
        })

    return resultado


# ---------- Sessoes ----------

@app.post("/sessoes", status_code=201)
def criar_sessao(body: SessaoCreate):
    paciente = db.get_paciente(body.paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")

    aberta = db.sessao_aberta_do_paciente(body.paciente_id)
    if aberta:
        raise HTTPException(
            status_code=409,
            detail="Já existe uma sessão aberta para este paciente. Encerre-a antes de iniciar uma nova.",
        )

    return db.criar_sessao(body.paciente_id)


@app.get("/sessoes/{sessao_id}")
def buscar_sessao(sessao_id: int):
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    chunks = db.get_chunks_sessao(sessao_id)
    consolidado = db.get_consolidado_sessao(sessao_id)

    return {**sessao, "chunks": chunks, "consolidado": consolidado}


@app.post("/sessoes/{sessao_id}/audio")
async def upload_audio(sessao_id: int, audio: UploadFile = File(...)):
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
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


@app.delete("/sessoes/{sessao_id}")
def cancelar_sessao(sessao_id: int):
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    chunks = db.get_chunks_sessao(sessao_id)
    # Sessão aberta sem áudio → cancela (hard delete)
    if sessao["status"] == "aberta" and not chunks:
        db.cancelar_sessao(sessao_id)
        return {"status": "cancelada", "sessao_id": sessao_id}
    # Qualquer outro caso → soft delete
    db.deletar_sessao(sessao_id)
    return {"status": "deletada", "sessao_id": sessao_id}


@app.post("/sessoes/{sessao_id}/adicionar-audio")
async def adicionar_audio_sessao_encerrada(sessao_id: int, audio: UploadFile = File(...)):
    """Adiciona áudio a uma sessão encerrada do mesmo dia, sem abater do pacote."""
    from datetime import date
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
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
        dados = await ai.consolidar_sessao([c["transcricao"] for c in chunks])
        db.salvar_consolidado(sessao_id, dados)
    except Exception:
        pass

    return {"chunk": chunk, "transcricao": transcricao}


@app.post("/sessoes/{sessao_id}/encerrar")
async def encerrar_sessao(sessao_id: int):
    sessao = db.get_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    if sessao["status"] != "aberta":
        raise HTTPException(status_code=400, detail="Sessão já está encerrada")

    chunks = db.get_chunks_sessao(sessao_id)
    if not chunks:
        raise HTTPException(
            status_code=400,
            detail="Nenhum áudio registrado nesta sessão. Grave pelo menos um áudio antes de encerrar.",
        )

    transcricoes = [c["transcricao"] for c in chunks]

    try:
        dados_consolidados = await ai.consolidar_sessao(transcricoes)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao consolidar com IA: {str(e)}")

    consolidado = db.salvar_consolidado(sessao_id, dados_consolidados)
    db.encerrar_sessao(sessao_id)

    # Detecção automática de procedimentos extras na transcrição
    transcricao_completa = "\n".join(transcricoes)
    nota = dados_consolidados.get("nota") or dados_consolidados.get("conduta")
    try:
        extras = await ai.detectar_procedimentos_extras(transcricao_completa, nota)
        for item in extras:
            db.adicionar_procedimento(
                sessao_id, sessao["paciente_id"],
                item["descricao"], item.get("valor"), None,
            )
    except Exception:
        pass  # detecção de extras é best-effort, não bloqueia o encerramento

    return {"consolidado": consolidado, "sessao_id": sessao_id, "status": "encerrada"}


@app.get("/pacientes/{paciente_id}/sessoes")
def listar_sessoes_paciente(paciente_id: int):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")

    sessoes = db.get_sessoes_paciente(paciente_id)

    # Enriquece cada sessão com seu consolidado, se existir
    result = []
    for s in sessoes:
        consolidado = db.get_consolidado_sessao(s["id"])
        result.append({**s, "consolidado": consolidado})

    return result


# ---------- Historico / IA ----------

@app.get("/faturamento/pacientes")
def faturamento_pacientes(mes: str | None = None, paciente_id: int | None = None, request: Request = None):
    return db.get_faturamento_pacientes(ano_mes=mes, paciente_id=paciente_id, owner_email=_owner_email(request))


@app.get("/billing")
def billing(mes: str | None = None):
    from datetime import date
    ano_mes = mes or date.today().strftime("%Y-%m")
    return {
        "mes_atual": db.get_billing_mes(ano_mes),
        "historico": db.get_billing_meses(),
    }


@app.get("/pacientes/{paciente_id}/resumo")
async def resumo_paciente(paciente_id: int):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")

    historico = db.get_historico_paciente(paciente_id)
    documentos = db.get_documentos_paciente(paciente_id)

    try:
        resumo = await ai.resumir_historico(historico, paciente, documentos)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao gerar resumo: {str(e)}")

    return {"paciente": paciente, "resumo": resumo}


@app.post("/pacientes/{paciente_id}/perguntar")
async def perguntar_ao_historico(paciente_id: int, body: PerguntaBody):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")

    if not body.pergunta.strip():
        raise HTTPException(status_code=400, detail="Pergunta não pode ser vazia")

    historico = db.get_historico_paciente(paciente_id)

    try:
        resposta = await ai.responder_pergunta(body.pergunta, historico, paciente)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao consultar IA: {str(e)}")

    return {"pergunta": body.pergunta, "resposta": resposta}


# ---------- Documentos ----------

@app.post("/pacientes/{paciente_id}/documentos", status_code=201)
async def upload_documento(paciente_id: int, arquivo: UploadFile = File(...)):
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
    except Exception:
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
            resumo = await ai.resumir_documento(texto)
        except Exception:
            pass

    doc = db.salvar_documento(paciente_id, arquivo.filename, nome_arquivo, resumo)
    return doc


@app.get("/pacientes/{paciente_id}/documentos")
def listar_documentos(paciente_id: int):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    return db.get_documentos_paciente(paciente_id)


@app.get("/documentos/{doc_id}/arquivo")
def servir_documento(doc_id: int):
    doc = db.get_documento(doc_id)
    if not doc or doc.get("deletado_em"):
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    caminho = os.path.join(db.DOCS_DIR, doc["caminho"])
    if not os.path.isfile(caminho):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no servidor")
    return FileResponse(caminho, media_type="application/pdf", filename=doc["nome_original"])


@app.delete("/documentos/{doc_id}", status_code=204)
def deletar_documento(doc_id: int):
    doc = db.get_documento(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    db.deletar_documento(doc_id)


# ---------- Pacotes ----------

@app.post("/pacientes/{paciente_id}/pacotes", status_code=201)
def criar_pacote(paciente_id: int, body: PacoteCreate):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    return db.criar_pacote(paciente_id, body.total_sessoes, body.valor_pago, body.data_pagamento, body.descricao)


@app.get("/pacientes/{paciente_id}/pacotes")
def listar_pacotes(paciente_id: int):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    return db.get_pacotes_paciente(paciente_id)


@app.delete("/pacotes/{pacote_id}", status_code=204)
def deletar_pacote(pacote_id: int):
    db.deletar_pacote(pacote_id)


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

    nf = db.emitir_nota_fiscal(
        paciente_id=body.paciente_id,
        paciente_nome=body.paciente_nome,
        valor_servico=body.valor_servico,
        descricao=body.descricao,
        competencia=body.competencia,
        dados_json=_json.dumps(dados, ensure_ascii=False),
        owner_email=_owner_email(request),
    )
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
def cancelar_nota_fiscal(nf_id: int):
    nf = db.get_nota_fiscal(nf_id)
    if not nf:
        raise HTTPException(status_code=404, detail="Nota fiscal não encontrada")
    db.cancelar_nota_fiscal(nf_id)


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
async def auth_google_login(body: GoogleLoginBody):
    """Verifica o credential do Google, cria/atualiza usuário e retorna JWT."""
    if not google_auth.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google SSO não configurado no servidor.")
    try:
        info = google_auth.verificar_google_token(body.credential)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Token Google inválido: {e}")
    email = info.get("email")
    nome  = info.get("name", email)
    foto  = info.get("picture")
    if not email:
        raise HTTPException(status_code=401, detail="E-mail não disponível no token Google.")
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    usuario = db.upsert_usuario(email, nome, foto, admin_email)
    if not usuario.get("ativo"):
        raise HTTPException(status_code=403, detail="Acesso pendente de aprovação do administrador.")
    token = google_auth.criar_jwt(email, nome, foto)
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
    return {"ok": True}


@app.post("/admin/usuarios/{email}/revogar")
def admin_revogar_usuario(email: str, request: Request):
    _verificar_admin(request)
    db.revogar_usuario(email)
    return {"ok": True}


# ---------- Auth WebAuthn ----------

@app.post("/auth/register/begin")
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
    _sessions[token] = _USERNAME
    return {"token": token, "message": "Dispositivo registrado com sucesso"}


@app.post("/auth/login/begin")
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
    _sessions[token] = _USERNAME
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

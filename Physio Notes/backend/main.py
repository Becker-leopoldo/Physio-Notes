import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

import database as db
import transcribe
import ai

# ---------- WebAuthn session store (in-memory) ----------
_challenges: dict[str, bytes] = {}   # username -> challenge bytes
_sessions: dict[str, str] = {}       # token -> username
_USERNAME = "fisioterapeuta"          # usuário fixo para o MVP

# WebAuthn: configurable via env vars for cloud deployments
# WEBAUTHN_RP_ID  = "meudominio.com"   (sem protocolo, sem porta)
# WEBAUTHN_ORIGIN = "https://meudominio.com"  (com protocolo, sem barra final)
_RP_ID = os.environ.get("WEBAUTHN_RP_ID", "localhost")
_ORIGIN_OVERRIDE = os.environ.get("WEBAUTHN_ORIGIN", "")  # se vazio, usa request.base_url


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


# ---------- Schemas ----------

class PacienteCreate(BaseModel):
    nome: str
    data_nascimento: str | None = None
    observacoes: str | None = None
    anamnese: str | None = None
    data_atendimento: str | None = None


class SessaoCreate(BaseModel):
    paciente_id: int


class PerguntaBody(BaseModel):
    pergunta: str


class ExtrairPacienteBody(BaseModel):
    transcricao: str


class RelatorioCREFITOBody(BaseModel):
    paciente_ids: list[int]


class PacoteCreate(BaseModel):
    total_sessoes: int
    valor_pago: float | None = None
    data_pagamento: str | None = None
    descricao: str | None = None


# ---------- Pacientes ----------

@app.post("/pacientes", status_code=201)
def criar_paciente(body: PacienteCreate):
    paciente = db.criar_paciente(body.nome, body.data_nascimento, body.observacoes, body.anamnese)
    return paciente


@app.get("/pacientes")
def listar_pacientes():
    return db.listar_pacientes()


@app.get("/pacientes/{paciente_id}")
def buscar_paciente(paciente_id: int):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    return paciente


class PacienteUpdate(BaseModel):
    nome: str
    data_nascimento: str | None = None
    anamnese: str | None = None


@app.delete("/pacientes/{paciente_id}", status_code=204)
def deletar_paciente(paciente_id: int):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    db.deletar_paciente(paciente_id)


@app.put("/pacientes/{paciente_id}")
def atualizar_paciente(paciente_id: int, body: PacienteUpdate):
    paciente = db.get_paciente(paciente_id)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    return db.atualizar_paciente(paciente_id, body.nome, body.data_nascimento, body.anamnese)


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
        rp_id=_RP_ID,
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

    origin = _ORIGIN_OVERRIDE or str(request.base_url).rstrip("/")

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
            expected_rp_id=_RP_ID,
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
        rp_id=_RP_ID,
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

    origin = _ORIGIN_OVERRIDE or str(request.base_url).rstrip("/")

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
            expected_rp_id=_RP_ID,
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

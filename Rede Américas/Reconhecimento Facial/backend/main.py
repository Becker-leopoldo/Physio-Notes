import asyncio
import uuid
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from models import AdmissaoRequest, CorenRequest, DataValidRequest, JobResponse, JobResult, JobStatus
from services.coren import CorenService
from services.datavalid import DataValidService

load_dotenv()

# ── Job store em memória ──────────────────────────────────────────────────────
# Estrutura: { job_id: { status, service, result, error } }
jobs: dict[str, dict] = {}

# ── Serviços disponíveis ──────────────────────────────────────────────────────
INFOSIMPLES_TOKEN = os.getenv("INFOSIMPLES_TOKEN")
SERPRO_KEY = os.getenv("SERPRO_CONSUMER_KEY")
SERPRO_SECRET = os.getenv("SERPRO_CONSUMER_SECRET")
SERPRO_ENV = os.getenv("SERPRO_ENV", "production")
SERPRO_DEMO_TOKEN = os.getenv("SERPRO_DEMO_TOKEN")

coren_service = CorenService(token=INFOSIMPLES_TOKEN)
datavalid_service = DataValidService(
    consumer_key=SERPRO_KEY,
    consumer_secret=SERPRO_SECRET,
    env=SERPRO_ENV,
    demo_token=SERPRO_DEMO_TOKEN,
)


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="POC Rede Américas — API Hub", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────
def new_job(service: str) -> str:
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": JobStatus.pending, "service": service, "result": None, "error": None}
    return job_id


async def run_job(job_id: str, service_name: str, coro):
    jobs[job_id]["status"] = JobStatus.processing
    try:
        result = await coro
        jobs[job_id]["status"] = JobStatus.done
        jobs[job_id]["result"] = result
    except Exception as e:
        tb = traceback.format_exc()
        logger.error("Job %s falhou:\n%s", job_id, tb)
        jobs[job_id]["status"] = JobStatus.error
        jobs[job_id]["error"] = tb


async def run_job_parallel(job_id: str, named_coros: dict):
    """
    Executa múltiplas coroutines em paralelo.
    Cada serviço grava seu resultado assim que conclui — resultado parcial
    disponível via GET /job/{id} enquanto os demais ainda processam.
    O job vai para 'done' quando todos terminam.
    """
    jobs[job_id]["status"] = JobStatus.processing
    jobs[job_id]["result"] = {}

    async def run_one(key: str, coro):
        try:
            jobs[job_id]["result"][key] = await coro
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("Service %s in job %s failed:\n%s", key, job_id, tb)
            jobs[job_id]["result"][key] = {"error": str(e)}

    await asyncio.gather(*[run_one(k, v) for k, v in named_coros.items()])
    jobs[job_id]["status"] = JobStatus.done


# ── Rotas gerais ──────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/job/{job_id}", response_model=JobResult)
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return JobResult(job_id=job_id, **job)


# ── Rotas de consulta ─────────────────────────────────────────────────────────

@app.post("/consulta/coren", response_model=JobResponse, status_code=202)
async def consulta_coren(body: CorenRequest, background_tasks: BackgroundTasks):
    if not body.cpf and not body.inscricao:
        raise HTTPException(status_code=422, detail="Informe cpf ou inscricao")

    params = {}
    if body.cpf:
        params["cpf"] = body.cpf
    if body.inscricao:
        params["inscricao"] = body.inscricao
    if body.nome_completo:
        params["nome_completo"] = body.nome_completo

    job_id = new_job("coren")
    background_tasks.add_task(run_job, job_id, "coren", coren_service.consultar(params))

    return JobResponse(job_id=job_id, status=JobStatus.pending, service="coren")


@app.post("/consulta/datavalid/facial", response_model=JobResponse, status_code=202)
async def consulta_datavalid_facial(body: DataValidRequest, background_tasks: BackgroundTasks):
    if not body.cpf or not body.foto:
        raise HTTPException(status_code=422, detail="Informe cpf e foto (base64)")

    params = {"cpf": body.cpf, "foto": body.foto}
    if body.nome:
        params["nome"] = body.nome
    if body.data_nascimento:
        params["data_nascimento"] = body.data_nascimento

    job_id = new_job("datavalid/facial")
    background_tasks.add_task(run_job, job_id, "datavalid/facial", datavalid_service.consultar(params))

    return JobResponse(job_id=job_id, status=JobStatus.pending, service="datavalid/facial")


@app.post("/consulta/admissao", response_model=JobResponse, status_code=202)
async def consulta_admissao(body: AdmissaoRequest, background_tasks: BackgroundTasks):
    """
    Dispara COREN + DataValid em paralelo em um único job.
    Retorna job_id imediatamente; resultado disponível via GET /job/{job_id}.
    """
    # COREN — sempre por número de inscrição
    coren_params: dict = {"inscricao": body.inscricao_coren}

    # DataValid — pf-completa (com foto) ou pf-basica (sem foto, só dados cadastrais)
    datavalid_params: dict = {"cpf": body.cpf, "nome": body.nome}
    if body.foto:
        datavalid_params["foto"] = body.foto
    for field in ("data_nascimento", "nome_mae", "nome_pai"):
        if getattr(body, field):
            datavalid_params[field] = getattr(body, field)

    if body.foto:
        # pf-completa suporta CNH e endereço, mas requer foto no demo
        cnh: dict = {}
        for field in ("categoria", "numero_registro", "data_primeira_habilitacao", "data_validade", "data_ultima_emissao"):
            val = getattr(body, f"cnh_{field}", None)
            if val:
                cnh[field] = val
        if cnh:
            datavalid_params["cnh"] = cnh

        endereco: dict = {}
        for field in ("cep", "logradouro", "numero", "bairro", "municipio", "uf"):
            val = getattr(body, f"endereco_{field}", None)
            if val:
                endereco[field] = val
        if endereco:
            datavalid_params["endereco"] = endereco

    dv_endpoint = "pf-completa" if body.foto else "pf-basica"

    job_id = new_job("admissao")
    background_tasks.add_task(
        run_job_parallel,
        job_id,
        {
            "coren":     coren_service.consultar(coren_params),
            "datavalid": datavalid_service.consultar(datavalid_params, endpoint=dv_endpoint),
        },
    )
    return JobResponse(job_id=job_id, status=JobStatus.pending, service="admissao")


# ── Para adicionar nova API ───────────────────────────────────────────────────
# 1. Crie services/nova_api.py herdando BaseService
# 2. Instancie o serviço acima (junto com coren_service)
# 3. Adicione uma rota @app.post("/consulta/nova_api") seguindo o mesmo padrão

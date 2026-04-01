from pydantic import BaseModel
from typing import Any, Optional
from enum import Enum


class JobStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    done = "done"
    error = "error"


class CorenRequest(BaseModel):
    cpf: Optional[str] = None
    inscricao: Optional[str] = None
    nome_completo: Optional[str] = None


class DataValidRequest(BaseModel):
    cpf: str
    foto: str                          # base64 da imagem
    nome: Optional[str] = None
    data_nascimento: Optional[str] = None  # "YYYY-MM-DD"


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    service: str


class AdmissaoRequest(BaseModel):
    # Dados do profissional
    cpf: str
    nome: str
    data_nascimento: Optional[str] = None   # "YYYY-MM-DD"
    foto: Optional[str] = None              # base64 da selfie (opcional)

    # COREN — número de inscrição obrigatório
    inscricao_coren: str

    # Validação RFB / CNH
    nome_mae: Optional[str] = None
    nome_pai: Optional[str] = None

    # CNH
    cnh_categoria: Optional[str] = None
    cnh_numero_registro: Optional[str] = None
    cnh_data_primeira_habilitacao: Optional[str] = None  # "YYYY-MM-DD"
    cnh_data_validade: Optional[str] = None              # "YYYY-MM-DD"
    cnh_data_ultima_emissao: Optional[str] = None        # "YYYY-MM-DD"

    # Endereço
    endereco_cep: Optional[str] = None
    endereco_logradouro: Optional[str] = None
    endereco_numero: Optional[str] = None
    endereco_bairro: Optional[str] = None
    endereco_municipio: Optional[str] = None
    endereco_uf: Optional[str] = None


class JobResult(BaseModel):
    job_id: str
    status: JobStatus
    service: str
    result: Optional[Any] = None
    error: Optional[str] = None

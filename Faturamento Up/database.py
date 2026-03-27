"""
Modelos SQLAlchemy para o sistema de faturamento UP IT.
"""
import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///faturamento.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class NotaFiscal(Base):
    __tablename__ = "notas_fiscais"

    id               = Column(Integer, primary_key=True, index=True)
    mes_ref          = Column(Integer, nullable=False)   # 1-12
    cliente          = Column(String, nullable=False)
    numero_nf        = Column(String)
    data_vencimento  = Column(DateTime)
    valor            = Column(Float, nullable=False)
    forma_pagamento  = Column(String)                    # PIX, TED, BOLETO
    status           = Column(String, default="ABERTO")  # ABERTO, PAGO, SEM_MATCH
    pago_em          = Column(DateTime)
    fonte_memo       = Column(Text)
    tipo_match       = Column(String)
    cr_amount        = Column(Float)
    created_at       = Column(DateTime, default=datetime.utcnow)
    deleted          = Column(Boolean, default=False)


class TransacaoOFX(Base):
    __tablename__ = "transacoes_ofx"

    id           = Column(Integer, primary_key=True, index=True)
    fitid        = Column(String, unique=True, index=True)
    date         = Column(DateTime)
    amount       = Column(Float)
    memo         = Column(Text)
    ofx_filename = Column(String)
    used         = Column(Boolean, default=False)
    mes_usado    = Column(String)   # nome do mês que consumiu este crédito
    created_at   = Column(DateTime, default=datetime.utcnow)


class AliasDB(Base):
    __tablename__ = "aliases"

    id           = Column(Integer, primary_key=True)
    chave        = Column(String, unique=True, index=True)  # CNPJ ou trecho de memo
    nome_cliente = Column(String)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)

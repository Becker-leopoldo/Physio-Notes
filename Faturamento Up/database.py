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


class ClienteCadastro(Base):
    __tablename__ = "clientes_cadastro"

    id              = Column(Integer, primary_key=True)
    nome            = Column(String, nullable=False)
    cnpj            = Column(String)
    email           = Column(String)
    telefone        = Column(String)
    contato         = Column(String)            # pessoa de contato
    forma_pagamento = Column(String)            # PIX, TED, BOLETO
    ativo           = Column(Boolean, default=True)
    created_at      = Column(DateTime, default=datetime.utcnow)


class ReceitaCadastro(Base):
    __tablename__ = "receitas_cadastro"

    id               = Column(Integer, primary_key=True)
    nome_projeto     = Column(String)                       # nome do projeto / serviço
    cliente          = Column(String, nullable=False)
    numero_proposta  = Column(String)                       # nº da proposta comercial
    tipo_pagamento   = Column(String)                       # PIX, TED, BOLETO
    tipo_receita     = Column(String)                       # Recorrente, Projeto, Avulso…
    valor_titulo     = Column(Float)                        # valor contratado (título)
    dia_vencimento   = Column(Integer)                      # dia do mês, ex: 10
    perc_reajuste    = Column(Float)                        # % de reajuste (ex: 5.5)
    tempo_reajuste   = Column(Integer)                      # meses até próximo reajuste
    valor_reajuste   = Column(Float)                        # valor após reajuste
    tempo_novo       = Column(Integer)                      # nova vigência em meses
    flag_reajuste    = Column(Boolean, default=False)       # reajuste pendente?
    ativo            = Column(Boolean, default=True)
    created_at       = Column(DateTime, default=datetime.utcnow)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate_receitas_columns():
    """
    Migra receitas_cadastro para o novo schema.
    Se a tabela antiga tiver a coluna 'valor' (schema v1), recria a tabela completa
    copiando os dados para o novo schema.
    Se já estiver no novo schema, apenas adiciona colunas faltantes.
    """
    if not DATABASE_URL.startswith("sqlite"):
        return
    db_path = DATABASE_URL.split("sqlite:///")[-1]
    import sqlite3
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(receitas_cadastro)")
            rows = cursor.fetchall()
            if not rows:
                return  # tabela não existe ainda; create_all cria do zero
            existing = {row[1] for row in rows}

            # Schema v1 detectado (tem 'valor' NOT NULL) → recria tabela
            if "valor" in existing:
                conn.execute("""
                    CREATE TABLE receitas_cadastro_new (
                        id             INTEGER PRIMARY KEY,
                        nome_projeto   VARCHAR,
                        cliente        VARCHAR NOT NULL,
                        numero_proposta VARCHAR,
                        tipo_pagamento VARCHAR,
                        tipo_receita   VARCHAR,
                        valor_titulo   FLOAT,
                        dia_vencimento INTEGER,
                        perc_reajuste  FLOAT,
                        tempo_reajuste INTEGER,
                        valor_reajuste FLOAT,
                        tempo_novo     INTEGER,
                        flag_reajuste  BOOLEAN DEFAULT 0,
                        ativo          BOOLEAN DEFAULT 1,
                        created_at     DATETIME
                    )
                """)
                # Copia dados mapeando colunas antigas → novas
                old_descricao = "descricao" if "descricao" in existing else "NULL"
                old_forma     = "forma_pagamento" if "forma_pagamento" in existing else "NULL"
                conn.execute(f"""
                    INSERT INTO receitas_cadastro_new
                        (id, nome_projeto, cliente, tipo_pagamento, valor_titulo,
                         dia_vencimento, ativo, created_at)
                    SELECT id, {old_descricao}, cliente, {old_forma}, valor,
                           dia_vencimento, ativo, created_at
                    FROM receitas_cadastro
                """)
                conn.execute("DROP TABLE receitas_cadastro")
                conn.execute("ALTER TABLE receitas_cadastro_new RENAME TO receitas_cadastro")
                return

            # Schema já novo → apenas adiciona colunas que faltam
            new_cols = [
                ("nome_projeto",    "VARCHAR"),
                ("numero_proposta", "VARCHAR"),
                ("tipo_pagamento",  "VARCHAR"),
                ("tipo_receita",    "VARCHAR"),
                ("valor_titulo",    "FLOAT"),
                ("perc_reajuste",   "FLOAT"),
                ("tempo_reajuste",  "INTEGER"),
                ("valor_reajuste",  "FLOAT"),
                ("tempo_novo",      "INTEGER"),
                ("flag_reajuste",   "BOOLEAN DEFAULT 0"),
            ]
            for col, typ in new_cols:
                if col not in existing:
                    conn.execute(f"ALTER TABLE receitas_cadastro ADD COLUMN {col} {typ}")
    except Exception:
        pass  # tabela não existe; create_all vai criar corretamente


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_receitas_columns()

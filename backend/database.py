import sqlite3
import os
import base64
import hashlib
import hmac as _hmac
import logging
from datetime import datetime, timezone
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("physio_notes")

# ---------- Constants ----------
STATUS_ABERTA = "aberta"
STATUS_ENCERRADA = "encerrada"
STATUS_CANCELADA = "cancelada"
_SQL_PRAGMA_PACIENTE = "PRAGMA table_info(paciente)"
_SQL_GET_PACIENTE_BY_ID = "SELECT * FROM paciente WHERE id = ?"
_SQL_OWNER_EMAIL_FILTER = "p.owner_email = ?"
_SQL_INSERT_PROCEDIMENTO_EXTRA = (
    "INSERT INTO procedimento_extra "
    "(sessao_id, paciente_id, descricao, valor, data, criado_em) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

# ---------- Criptografia de campos PII (CPF, endereço) ----------
# Estratégia: Blind Index
#   cpf      → Fernet (AES-128-CBC + HMAC, IV aleatório) — para exibição/leitura
#   cpf_hash → HMAC-SHA256 determinístico             — para unicidade via índice DB
# Chaves derivadas de FIELD_ENCRYPTION_KEY com contextos distintos ("enc:" e "hash:")

_fernet_instance = None
_hash_key: bytes | None = None
_ENC_PREFIX = "enc:"


def _get_fernet():
    global _fernet_instance
    if _fernet_instance is None:
        raw = os.getenv("FIELD_ENCRYPTION_KEY", "")
        if not raw:
            raise ValueError("Falha de Segurança (LGPD): A variável FIELD_ENCRYPTION_KEY não está configurada! Criptografia de PII é obrigatória.")
        from cryptography.fernet import Fernet
        enc_key = base64.urlsafe_b64encode(hashlib.sha256(("enc:" + raw).encode()).digest())
        _fernet_instance = Fernet(enc_key)
    return _fernet_instance


def _get_hash_key() -> bytes:
    global _hash_key
    if _hash_key is None:
        raw = os.getenv("FIELD_ENCRYPTION_KEY", "")
        if not raw:
            raise ValueError("Falha de Segurança (LGPD): A variável FIELD_ENCRYPTION_KEY não está configurada! Hashing cego de CPF é obrigatório.")
        _hash_key = hashlib.sha256(("hash:" + raw).encode()).digest()
    return _hash_key


def _encrypt_field(value: str | None) -> str | None:
    if not value:
        return value
    if value.startswith(_ENC_PREFIX):
        return value  # já criptografado
    f = _get_fernet()
    return _ENC_PREFIX + f.encrypt(value.encode()).decode()


def _decrypt_field(value: str | None) -> str | None:
    if not value or not value.startswith(_ENC_PREFIX):
        return value  # plaintext ou None (mantém legibilidade de dados antigos)
    f = _get_fernet()
    try:
        return f.decrypt(value[len(_ENC_PREFIX):].encode()).decode()
    except (InvalidToken, ValueError):
        return value


def _cpf_hash(cpf: str | None) -> str | None:
    """HMAC-SHA256 determinístico do CPF — usado como blind index para unicidade."""
    if not cpf:
        return None
    key = _get_hash_key()
    return _hmac.new(key, cpf.strip().encode(), hashlib.sha256).hexdigest()


def _decrypt_paciente(p: dict | None) -> dict | None:
    if not p:
        return p
    return {**p, "cpf": _decrypt_field(p.get("cpf")), "endereco": _decrypt_field(p.get("endereco"))}

DB_PATH = os.getenv("DB_PATH") or os.path.join(os.path.dirname(__file__), "physio_notes.db")
DOCS_DIR = os.path.join(os.path.dirname(DB_PATH), "documentos")
os.makedirs(DOCS_DIR, exist_ok=True)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Flag para evitar PRAGMA + ALTER TABLE repetido a cada chamada de IA
_api_uso_cols_ok: bool = False


def _ensure_api_uso_cols(conn) -> None:
    """Garante que as colunas owner_email, sec_email e paciente_nome existem em api_uso.
    Executa PRAGMA table_info apenas uma vez por processo (flag global)."""
    global _api_uso_cols_ok
    if _api_uso_cols_ok:
        return
    cols = [r[1] for r in conn.execute("PRAGMA table_info(api_uso)").fetchall()]
    if "owner_email" not in cols:
        conn.execute("ALTER TABLE api_uso ADD COLUMN owner_email TEXT")
    if "sec_email" not in cols:
        conn.execute("ALTER TABLE api_uso ADD COLUMN sec_email TEXT")
    if "paciente_nome" not in cols:
        conn.execute("ALTER TABLE api_uso ADD COLUMN paciente_nome TEXT")
    conn.commit()
    _api_uso_cols_ok = True


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS paciente (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                nome            TEXT    NOT NULL,
                data_nascimento TEXT,
                observacoes     TEXT,
                anamnese        TEXT,
                criado_em       TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessao (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id INTEGER NOT NULL REFERENCES paciente(id),
                data        TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'aberta',
                criado_em   TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audio_chunk (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                sessao_id   INTEGER NOT NULL REFERENCES sessao(id),
                transcricao TEXT    NOT NULL,
                timestamp   TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessao_consolidada (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sessao_id       INTEGER NOT NULL UNIQUE REFERENCES sessao(id),
                queixa          TEXT,
                evolucao        TEXT,
                conduta         TEXT,
                observacoes     TEXT,
                proximos_passos TEXT,
                raw_json        TEXT,
                criado_em       TEXT    NOT NULL
            );
        """)


def _migrate():
    """Adiciona colunas novas sem quebrar bancos existentes."""
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute(_SQL_PRAGMA_PACIENTE).fetchall()]
        if "anamnese" not in cols:
            conn.execute("ALTER TABLE paciente ADD COLUMN anamnese TEXT")
        if "cpf" not in cols:
            conn.execute("ALTER TABLE paciente ADD COLUMN cpf TEXT")
        if "endereco" not in cols:
            conn.execute("ALTER TABLE paciente ADD COLUMN endereco TEXT")
        # Multi-tenant: dono de cada paciente
        cols_pac = [r[1] for r in conn.execute(_SQL_PRAGMA_PACIENTE).fetchall()]
        if "owner_email" not in cols_pac:
            conn.execute("ALTER TABLE paciente ADD COLUMN owner_email TEXT")
        if "conduta_tratamento" not in cols_pac:
            conn.execute("ALTER TABLE paciente ADD COLUMN conduta_tratamento TEXT")

        # Blind index: cpf_hash (HMAC determinístico) + índice único por owner
        if "cpf_hash" not in cols:
            conn.execute("ALTER TABLE paciente ADD COLUMN cpf_hash TEXT")

        # Sugestão da IA (always overwrite, never accumulate)
        cols_pac2 = [r[1] for r in conn.execute(_SQL_PRAGMA_PACIENTE).fetchall()]
        if "sugestao_ia" not in cols_pac2:
            conn.execute("ALTER TABLE paciente ADD COLUMN sugestao_ia TEXT")
        if "sugestao_ia_em" not in cols_pac2:
            conn.execute("ALTER TABLE paciente ADD COLUMN sugestao_ia_em TEXT")

        # Dados de contato e convênio (importação de cadastro externo)
        cols_pac3 = [r[1] for r in conn.execute(_SQL_PRAGMA_PACIENTE).fetchall()]
        if "telefone" not in cols_pac3:
            conn.execute("ALTER TABLE paciente ADD COLUMN telefone TEXT")
        if "convenio" not in cols_pac3:
            conn.execute("ALTER TABLE paciente ADD COLUMN convenio TEXT")
        if "ultima_consulta" not in cols_pac3:
            conn.execute("ALTER TABLE paciente ADD COLUMN ultima_consulta TEXT")

        # Endereço estruturado (CEP + logradouro + número + bairro + cidade + estado)
        cols_pac4 = [r[1] for r in conn.execute(_SQL_PRAGMA_PACIENTE).fetchall()]
        for col in ("cep", "logradouro", "numero", "bairro", "cidade", "estado"):
            if col not in cols_pac4:
                conn.execute(f"ALTER TABLE paciente ADD COLUMN {col} TEXT")

        # Soft delete em paciente deve vir antes do índice que referencia deletado_em
        cols_del = [r[1] for r in conn.execute(_SQL_PRAGMA_PACIENTE).fetchall()]
        if "deletado_em" not in cols_del:
            conn.execute("ALTER TABLE paciente ADD COLUMN deletado_em TEXT")

        conn.execute("DROP INDEX IF EXISTS idx_paciente_cpf_owner")
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_paciente_cpf_hash_owner
            ON paciente(cpf_hash, owner_email)
            WHERE cpf_hash IS NOT NULL AND deletado_em IS NULL
        """)
        conn.commit()

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_uso (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo          TEXT    NOT NULL,
                modelo        TEXT    NOT NULL,
                input_tokens  INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                custo_usd     REAL    NOT NULL DEFAULT 0,
                criado_em     TEXT    NOT NULL
            )
        """)

        # Soft delete em sessao (paciente já foi tratado acima)
        cols_sessao = [r[1] for r in conn.execute("PRAGMA table_info(sessao)").fetchall()]
        if "deletado_em" not in cols_sessao:
            conn.execute("ALTER TABLE sessao ADD COLUMN deletado_em TEXT")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS documento (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id   INTEGER NOT NULL REFERENCES paciente(id),
                nome_original TEXT    NOT NULL,
                caminho       TEXT    NOT NULL,
                resumo_ia     TEXT,
                criado_em     TEXT    NOT NULL
            )
        """)

        # Coluna nota na sessao_consolidada
        cols_sc = [r[1] for r in conn.execute("PRAGMA table_info(sessao_consolidada)").fetchall()]
        if "nota" not in cols_sc:
            conn.execute("ALTER TABLE sessao_consolidada ADD COLUMN nota TEXT")

        # Soft delete em documento
        cols_doc = [r[1] for r in conn.execute("PRAGMA table_info(documento)").fetchall()]
        if "deletado_em" not in cols_doc:
            conn.execute("ALTER TABLE documento ADD COLUMN deletado_em TEXT")

        # (owner_email em nota_fiscal é adicionado após o CREATE TABLE abaixo)

        # Pacotes de sessões
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pacote (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id     INTEGER NOT NULL REFERENCES paciente(id),
                total_sessoes   INTEGER NOT NULL,
                sessoes_usadas  INTEGER NOT NULL DEFAULT 0,
                pago            BOOLEAN NOT NULL DEFAULT 1,
                valor_pago      REAL,
                data_pagamento  TEXT,
                descricao       TEXT,
                criado_em       TEXT    NOT NULL,
                deletado_em     TEXT
            )
        """)
        try:
            conn.execute("ALTER TABLE pacote ADD COLUMN pago BOOLEAN DEFAULT 1;")
        except sqlite3.Error:
            pass

        # Procedimentos extras por sessão
        conn.execute("""
            CREATE TABLE IF NOT EXISTS procedimento_extra (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                sessao_id   INTEGER NOT NULL REFERENCES sessao(id),
                paciente_id INTEGER NOT NULL REFERENCES paciente(id),
                descricao   TEXT    NOT NULL,
                valor       REAL,
                data        TEXT,
                criado_em   TEXT    NOT NULL,
                deletado_em TEXT
            )
        """)

        # Notas Fiscais de Serviço (demo)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nota_fiscal (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                numero              TEXT    NOT NULL,
                paciente_id         INTEGER REFERENCES paciente(id),
                paciente_nome       TEXT    NOT NULL,
                valor_servico       REAL    NOT NULL,
                descricao           TEXT    NOT NULL,
                competencia         TEXT,
                data_emissao        TEXT    NOT NULL,
                codigo_verificacao  TEXT    NOT NULL,
                dados_json          TEXT    NOT NULL,
                status              TEXT    NOT NULL DEFAULT 'emitida',
                criado_em           TEXT    NOT NULL,
                deletado_em         TEXT
            )
        """)

        # Multi-tenant: owner em nota_fiscal (após CREATE TABLE)
        cols_nf = [r[1] for r in conn.execute("PRAGMA table_info(nota_fiscal)").fetchall()]
        if "owner_email" not in cols_nf:
            conn.execute("ALTER TABLE nota_fiscal ADD COLUMN owner_email TEXT")

        # WebAuthn
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usuario (
                id        TEXT PRIMARY KEY,
                username  TEXT NOT NULL UNIQUE,
                criado_em TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS webauthn_credential (
                id          TEXT PRIMARY KEY,
                usuario_id  TEXT NOT NULL REFERENCES usuario(id),
                public_key  BLOB NOT NULL,
                sign_count  INTEGER NOT NULL DEFAULT 0,
                criado_em   TEXT NOT NULL
            )
        """)

        # Audit log
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                criado_em   TEXT    NOT NULL,
                owner_email TEXT,
                acao        TEXT    NOT NULL,
                detalhe     TEXT,
                ip          TEXT
            )
        """)

        # Garante colunas owner_email/sec_email/paciente_nome em api_uso antes dos índices
        _ensure_api_uso_cols(conn)

        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_sessao_paciente_id ON sessao(paciente_id);
            CREATE INDEX IF NOT EXISTS idx_sessao_criado_em ON sessao(criado_em);
            CREATE INDEX IF NOT EXISTS idx_audio_chunk_sessao_id ON audio_chunk(sessao_id);
            CREATE INDEX IF NOT EXISTS idx_pacote_paciente_id ON pacote(paciente_id);
            CREATE INDEX IF NOT EXISTS idx_paciente_owner_email ON paciente(owner_email);
            CREATE INDEX IF NOT EXISTS idx_api_uso_criado_em ON api_uso(criado_em);
            CREATE INDEX IF NOT EXISTS idx_api_uso_owner_email ON api_uso(owner_email);
            CREATE INDEX IF NOT EXISTS idx_documento_paciente_id ON documento(paciente_id);
            CREATE INDEX IF NOT EXISTS idx_audit_log_criado_em ON audit_log(criado_em);
            CREATE INDEX IF NOT EXISTS idx_audit_log_owner_email ON audit_log(owner_email);
        """)
        conn.commit()

    _migrar_criptografar_pii()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict:
    return dict(row) if row else None


# ---------- Audit Log ----------

def registrar_audit(owner_email: str | None, acao: str, detalhe: str | None = None, ip: str | None = None):
    """Registra um evento de auditoria. Fire-and-forget — nunca levanta exceção."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO audit_log (criado_em, owner_email, acao, detalhe, ip) VALUES (?, ?, ?, ?, ?)",
                (_now(), owner_email, acao, detalhe, ip),
            )
            conn.commit()
    except sqlite3.Error as exc:
        logger.warning("registrar_audit: falha ao gravar acao=%s: %s", acao, exc)


def get_audit_log(owner_email: str | None = None, limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        if owner_email:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE owner_email = ? ORDER BY criado_em DESC LIMIT ?",
                (owner_email, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY criado_em DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ---------- Paciente ----------

def criar_paciente(
    nome: str, data_nascimento: str | None, observacoes: str | None,
    anamnese: str | None = None, cpf: str | None = None, endereco: str | None = None,
    owner_email: str | None = None, conduta_tratamento: str | None = None,
    telefone: str | None = None, convenio: str | None = None, ultima_consulta: str | None = None,
    cep: str | None = None, logradouro: str | None = None, numero: str | None = None,
    bairro: str | None = None, cidade: str | None = None, estado: str | None = None,
) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO paciente
               (nome, data_nascimento, observacoes, anamnese, cpf, cpf_hash, endereco,
                owner_email, conduta_tratamento, telefone, convenio, ultima_consulta,
                cep, logradouro, numero, bairro, cidade, estado, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (nome, data_nascimento, observacoes, anamnese, _encrypt_field(cpf), _cpf_hash(cpf),
             _encrypt_field(endereco), owner_email, conduta_tratamento, telefone, convenio,
             ultima_consulta, cep, logradouro, numero, bairro, cidade, estado, _now()),
        )
        conn.commit()
        return _decrypt_paciente(_row_to_dict(conn.execute(_SQL_GET_PACIENTE_BY_ID, (cur.lastrowid,)).fetchone()))


def atualizar_paciente(
    paciente_id: int, nome: str, data_nascimento: str | None, anamnese: str | None,
    cpf: str | None = None, endereco: str | None = None, conduta_tratamento: str | None = None,
    telefone: str | None = None, convenio: str | None = None,
    cep: str | None = None, logradouro: str | None = None, numero: str | None = None,
    bairro: str | None = None, cidade: str | None = None, estado: str | None = None,
) -> dict:
    with get_conn() as conn:
        conn.execute(
            """UPDATE paciente SET
               nome=?, data_nascimento=?, anamnese=?, cpf=?, cpf_hash=?, endereco=?,
               conduta_tratamento=?, telefone=?, convenio=?,
               cep=?, logradouro=?, numero=?, bairro=?, cidade=?, estado=?
               WHERE id=?""",
            (nome, data_nascimento, anamnese, _encrypt_field(cpf), _cpf_hash(cpf), _encrypt_field(endereco),
             conduta_tratamento, telefone, convenio,
             cep, logradouro, numero, bairro, cidade, estado, paciente_id),
        )
        conn.commit()
        return _decrypt_paciente(_row_to_dict(conn.execute(_SQL_GET_PACIENTE_BY_ID, (paciente_id,)).fetchone()))


def listar_pacientes(owner_email: str | None = None) -> list[dict]:
    with get_conn() as conn:
        owner_filter = "AND p.owner_email = ?" if owner_email else ""
        params = [owner_email] if owner_email else []
        rows = conn.execute(f"""
            SELECT p.*,
                   (SELECT (pk.total_sessoes - pk.sessoes_usadas)
                    FROM pacote pk
                    WHERE pk.paciente_id = p.id AND pk.deletado_em IS NULL
                      AND pk.sessoes_usadas < pk.total_sessoes
                    ORDER BY pk.criado_em DESC LIMIT 1) AS sessoes_restantes,
                   (SELECT pk2.total_sessoes
                    FROM pacote pk2
                    WHERE pk2.paciente_id = p.id AND pk2.deletado_em IS NULL
                      AND pk2.sessoes_usadas < pk2.total_sessoes
                    ORDER BY pk2.criado_em DESC LIMIT 1) AS pacote_total
            FROM paciente p
            WHERE p.deletado_em IS NULL {owner_filter} ORDER BY p.nome COLLATE NOCASE
        """, params).fetchall()
        return [_decrypt_paciente(_row_to_dict(r)) for r in rows]


def deletar_paciente(paciente_id: int):
    """Soft delete: marca paciente e todas as suas sessões como deletados."""
    now = _now()
    with get_conn() as conn:
        conn.execute("UPDATE paciente SET deletado_em = ? WHERE id = ?", (now, paciente_id))
        conn.execute("UPDATE sessao SET deletado_em = ? WHERE paciente_id = ? AND deletado_em IS NULL", (now, paciente_id))
        conn.commit()


def get_paciente(paciente_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(_SQL_GET_PACIENTE_BY_ID, (paciente_id,)).fetchone()
        return _decrypt_paciente(_row_to_dict(row))


def salvar_sugestao_ia(paciente_id: int, sugestao_json: str) -> None:
    """Sobrescreve (nunca acumula) a sugestão da IA para o paciente."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE paciente SET sugestao_ia = ?, sugestao_ia_em = ? WHERE id = ?",
            (sugestao_json, _now(), paciente_id),
        )
        conn.commit()


# ---------- Sessao ----------

def criar_sessao(paciente_id: int, data: str | None = None) -> dict:
    from datetime import date
    data = data or date.today().isoformat()
    now = _now()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sessao (paciente_id, data, status, criado_em) VALUES (?, ?, 'aberta', ?)",
            (paciente_id, data, now),
        )
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM sessao WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_sessao(sessao_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sessao WHERE id = ?", (sessao_id,)).fetchone()
        return _row_to_dict(row)


def deletar_sessao(sessao_id: int):
    """Soft delete de uma sessão."""
    with get_conn() as conn:
        conn.execute("UPDATE sessao SET deletado_em = ? WHERE id = ?", (_now(), sessao_id))
        conn.commit()


def get_agenda_owner(owner_email: str | None, ano_mes: str | None = None) -> list[dict]:
    """Retorna todas as sessões do owner com nome do paciente, opcionalmente filtradas por mês."""
    with get_conn() as conn:
        where = ["s.deletado_em IS NULL"]
        params: list = []
        if owner_email:
            where.append(_SQL_OWNER_EMAIL_FILTER)
            params.append(owner_email)
        if ano_mes:
            where.append("strftime('%Y-%m', s.data) = ?")
            params.append(ano_mes)
        rows = conn.execute(f"""
            SELECT s.id, s.paciente_id, s.data, s.status, s.criado_em,
                   p.nome AS paciente_nome
            FROM sessao s
            JOIN paciente p ON p.id = s.paciente_id
            WHERE {" AND ".join(where)}
            ORDER BY s.data DESC, s.criado_em DESC
        """, params).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_sessoes_paciente(paciente_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sessao WHERE paciente_id = ? AND deletado_em IS NULL ORDER BY criado_em DESC",
            (paciente_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_sessoes_com_consolidado(paciente_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT s.*,
                   sc.id AS cons_id,
                   sc.nota AS cons_nota,
                   sc.queixa AS cons_queixa,
                   sc.evolucao AS cons_evolucao,
                   sc.conduta AS cons_conduta,
                   sc.observacoes AS cons_observacoes,
                   sc.proximos_passos AS cons_proximos_passos,
                   sc.criado_em AS cons_criado_em
            FROM sessao s
            LEFT JOIN sessao_consolidada sc ON sc.sessao_id = s.id
            WHERE s.paciente_id = ? AND s.deletado_em IS NULL
            ORDER BY s.criado_em DESC
        """, (paciente_id,)).fetchall()
        result = []
        for r in rows:
            d = _row_to_dict(r)
            cons = {k[5:]: v for k, v in d.items() if k.startswith("cons_") and v is not None}
            for k in [k for k in d if k.startswith("cons_")]:
                del d[k]
            d["consolidado"] = cons if cons else None
            result.append(d)
        return result


def sessao_aberta_do_paciente(paciente_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sessao WHERE paciente_id = ? AND status = 'aberta' AND deletado_em IS NULL ORDER BY criado_em DESC LIMIT 1",
            (paciente_id,),
        ).fetchone()
        return _row_to_dict(row)


def _usar_sessao_pacote(conn, paciente_id: int) -> bool:
    """Abate 1 sessão do pacote ativo do paciente. Retorna True se havia pacote."""
    row = conn.execute(
        """SELECT id FROM pacote WHERE paciente_id = ? AND deletado_em IS NULL
           AND sessoes_usadas < total_sessoes ORDER BY criado_em DESC LIMIT 1""",
        (paciente_id,)
    ).fetchone()
    if row:
        conn.execute("UPDATE pacote SET sessoes_usadas = sessoes_usadas + 1 WHERE id = ?", (row["id"],))
        return True
    return False


def encerrar_sessao(sessao_id: int, owner_email: str | None = None, cobrar: bool = True, valor_override: float | None = None) -> dict:
    """Encerra sessão. Se não há pacote ativo e cobrar=True, cria procedimento_extra.
    valor_override tem prioridade sobre o valor configurado. Retorna {teve_pacote, sessao_avulsa_valor, cobrar}."""
    from datetime import date
    with get_conn() as conn:
        sessao = conn.execute("SELECT paciente_id, data FROM sessao WHERE id = ?", (sessao_id,)).fetchone()
        cur = conn.execute(
            "UPDATE sessao SET status = 'encerrada' WHERE id = ? AND status = 'aberta'",
            (sessao_id,)
        )
        if cur.rowcount == 0:
            return {"teve_pacote": True, "sessao_avulsa_valor": None, "_ja_encerrada": True, "cobrar": cobrar}
        sessao_avulsa_valor = None
        if sessao:
            data_sessao = sessao["data"] or date.today().isoformat()
            conn.execute(
                "UPDATE paciente SET ultima_consulta = ? WHERE id = ?",
                (data_sessao, sessao["paciente_id"]),
            )
            teve_pacote = _usar_sessao_pacote(conn, sessao["paciente_id"])
            if not teve_pacote and owner_email and cobrar:
                valor = valor_override
                if not valor:
                    row_cfg = conn.execute(
                        "SELECT valor_sessao_avulsa FROM usuario_google WHERE email = ?", (owner_email,)
                    ).fetchone()
                    valor = row_cfg["valor_sessao_avulsa"] if row_cfg else None
                if valor and valor > 0:
                    data_sessao = sessao["data"] or date.today().isoformat()
                    conn.execute(
                        _SQL_INSERT_PROCEDIMENTO_EXTRA,
                        (sessao_id, sessao["paciente_id"], "Sessão avulsa", valor, data_sessao, _now()),
                    )
                    sessao_avulsa_valor = valor
        conn.commit()
        return {"teve_pacote": teve_pacote if sessao else True, "sessao_avulsa_valor": sessao_avulsa_valor, "cobrar": cobrar}


def cancelar_sessao(sessao_id: int):
    """Remove sessão aberta sem áudio (criada por engano)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM sessao WHERE id = ? AND status = 'aberta'", (sessao_id,))
        conn.commit()


def registrar_cancelamento(
    sessao_id: int,
    cobrar: bool,
    valor: float | None,
    complemento: str | None,
    owner_email: str | None = None,
) -> None:
    """Marca sessão como cancelada, registra nota e opcionalmente gera cobrança de cancelamento."""
    from datetime import date as _date
    with get_conn() as conn:
        sessao = conn.execute("SELECT paciente_id, data FROM sessao WHERE id = ?", (sessao_id,)).fetchone()
        if not sessao:
            return
        conn.execute(
            "UPDATE sessao SET status = 'cancelada' WHERE id = ? AND status = 'aberta'",
            (sessao_id,),
        )
        # Nota automática de cancelamento
        nota = "Sessão cancelada pelo paciente."
        if complemento and complemento.strip():
            nota += f" {complemento.strip()}"
        # Insere ou atualiza consolidado
        existe = conn.execute(
            "SELECT id FROM sessao_consolidada WHERE sessao_id = ?", (sessao_id,)
        ).fetchone()
        if existe:
            conn.execute(
                "UPDATE sessao_consolidada SET nota = ? WHERE sessao_id = ?",
                (nota, sessao_id),
            )
        else:
            conn.execute(
                "INSERT INTO sessao_consolidada (sessao_id, nota, criado_em) VALUES (?, ?, ?)",
                (sessao_id, nota, _now()),
            )
        # Cobrança de cancelamento
        if cobrar and valor and valor > 0:
            data_sessao = sessao["data"] or _date.today().isoformat()
            conn.execute(
                _SQL_INSERT_PROCEDIMENTO_EXTRA,
                (sessao_id, sessao["paciente_id"], "Taxa de cancelamento", valor, data_sessao, _now()),
            )
        conn.commit()


# ---------- Audio Chunk ----------

def add_audio_chunk(sessao_id: int, transcricao: str) -> dict:
    now = _now()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO audio_chunk (sessao_id, transcricao, timestamp) VALUES (?, ?, ?)",
            (sessao_id, transcricao, now),
        )
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM audio_chunk WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_chunks_sessao(sessao_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM audio_chunk WHERE sessao_id = ? ORDER BY timestamp",
            (sessao_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ---------- Consolidado ----------

def salvar_consolidado(sessao_id: int, dados: dict) -> dict:
    now = _now()
    import json
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO sessao_consolidada
               (sessao_id, nota, queixa, evolucao, conduta, observacoes, proximos_passos, raw_json, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sessao_id) DO UPDATE SET
               nota=excluded.nota,
               queixa=excluded.queixa,
               evolucao=excluded.evolucao,
               conduta=excluded.conduta,
               observacoes=excluded.observacoes,
               proximos_passos=excluded.proximos_passos,
               raw_json=excluded.raw_json,
               criado_em=excluded.criado_em""",
            (
                sessao_id,
                dados.get("nota"),
                dados.get("queixa"),
                dados.get("evolucao"),
                dados.get("conduta"),
                dados.get("observacoes"),
                dados.get("proximos_passos"),
                json.dumps(dados, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM sessao_consolidada WHERE sessao_id = ?", (sessao_id,)).fetchone()
        return _row_to_dict(row)


def get_consolidado_sessao(sessao_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sessao_consolidada WHERE sessao_id = ?", (sessao_id,)
        ).fetchone()
        return _row_to_dict(row)


def get_historico_paciente(paciente_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.*, sc.nota, sc.queixa, sc.evolucao, sc.conduta,
                      sc.observacoes AS consolidado_observacoes,
                      sc.proximos_passos, sc.criado_em AS consolidado_criado_em
               FROM sessao s
               LEFT JOIN sessao_consolidada sc ON sc.sessao_id = s.id
               WHERE s.paciente_id = ? AND s.deletado_em IS NULL
               ORDER BY s.criado_em DESC""",
            (paciente_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ---------- Billing ----------

def registrar_uso(tipo: str, modelo: str, input_tokens: int, output_tokens: int, custo_usd: float, owner_email: str | None = None, sec_email: str | None = None, paciente_nome: str | None = None):
    with get_conn() as conn:
        _ensure_api_uso_cols(conn)
        conn.execute(
            "INSERT INTO api_uso (tipo, modelo, input_tokens, output_tokens, custo_usd, owner_email, sec_email, paciente_nome, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tipo, modelo, input_tokens, output_tokens, custo_usd, owner_email, sec_email, paciente_nome, _now()),
        )
        conn.commit()


def get_billing_mes(ano_mes: str, owner_email: str | None = None) -> dict:
    """ano_mes no formato YYYY-MM. Retorna totais, breakdown por tipo e por origem (fisio vs secretaria)."""
    owner_filter = "AND owner_email = ?" if owner_email else ""
    params_mes = [ano_mes, owner_email] if owner_email else [ano_mes]
    with get_conn() as conn:
        totais = conn.execute(
            f"""SELECT COUNT(*) as total_chamadas,
                      SUM(input_tokens) as total_input,
                      SUM(output_tokens) as total_output,
                      SUM(custo_usd) as total_usd,
                      COUNT(DISTINCT DATE(criado_em)) as dias_com_uso
               FROM api_uso WHERE strftime('%Y-%m', criado_em) = ? {owner_filter}""",
            params_mes,
        ).fetchone()

        por_tipo = conn.execute(
            f"""SELECT tipo,
                      COUNT(*) as chamadas,
                      SUM(input_tokens) as input_tokens,
                      SUM(output_tokens) as output_tokens,
                      ROUND(SUM(custo_usd), 6) as custo_usd
               FROM api_uso WHERE strftime('%Y-%m', criado_em) = ? {owner_filter}
               GROUP BY tipo ORDER BY custo_usd DESC""",
            params_mes,
        ).fetchall()

        _ensure_api_uso_cols(conn)

        # Breakdown por origem: fisio (sec_email IS NULL) vs secretaria (sec_email IS NOT NULL)
        por_origem = conn.execute(
            f"""SELECT
                  CASE WHEN sec_email IS NULL THEN 'fisio' ELSE 'secretaria' END as origem,
                  COUNT(*) as chamadas,
                  SUM(input_tokens) as input_tokens,
                  SUM(output_tokens) as output_tokens,
                  ROUND(SUM(custo_usd), 6) as custo_usd,
                  sec_email
               FROM api_uso WHERE strftime('%Y-%m', criado_em) = ? {owner_filter}
               GROUP BY origem, sec_email""",
            params_mes,
        ).fetchall()

        return {
            "mes": ano_mes,
            "total_chamadas": totais["total_chamadas"] or 0,
            "total_input_tokens": totais["total_input"] or 0,
            "total_output_tokens": totais["total_output"] or 0,
            "total_usd": round(totais["total_usd"] or 0, 6),
            "dias_com_uso": totais["dias_com_uso"] or 0,
            "por_tipo": [_row_to_dict(r) for r in por_tipo],
            "por_origem": [_row_to_dict(r) for r in por_origem],
        }


def get_billing_meses(owner_email: str | None = None) -> list[dict]:
    """Retorna lista de meses com totais, do mais recente ao mais antigo."""
    owner_filter = "WHERE owner_email = ?" if owner_email else ""
    params = [owner_email] if owner_email else []
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT strftime('%Y-%m', criado_em) as mes,
                      COUNT(*) as total_chamadas,
                      SUM(input_tokens) as total_input_tokens,
                      SUM(output_tokens) as total_output_tokens,
                      ROUND(SUM(custo_usd), 6) as total_usd
               FROM api_uso {owner_filter}
               GROUP BY mes ORDER BY mes DESC""",
            params,
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_gasto_hoje_usd(owner_email: str, hoje: str) -> float:
    """Retorna soma de custo_usd do owner hoje (YYYY-MM-DD)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(custo_usd), 0) FROM api_uso WHERE owner_email = ? AND DATE(criado_em) = ?",
            (owner_email, hoje),
        ).fetchone()
    return float(row[0]) if row else 0.0


def get_billing_por_usuario(ano_mes: str) -> list[dict]:
    """Admin: retorna custo agrupado por owner_email no mês."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                a.owner_email,
                COALESCE(u.nome, a.owner_email) AS nome,
                COUNT(*) AS total_chamadas,
                SUM(a.input_tokens)  AS total_input_tokens,
                SUM(a.output_tokens) AS total_output_tokens,
                ROUND(SUM(a.custo_usd), 6) AS total_usd
            FROM api_uso a
            LEFT JOIN usuario_google u ON u.email = a.owner_email
            WHERE strftime('%Y-%m', a.criado_em) = ?
              AND a.owner_email IS NOT NULL
            GROUP BY a.owner_email
            ORDER BY total_usd DESC
        """, (ano_mes,)).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_activity_log(owner_email: str, mes: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    """Retorna log detalhado de uso de IA para um owner, paginado.
    mes: 'YYYY-MM' opcional. Retorna {total, items: [...]}."""
    mes_filter = "AND strftime('%Y-%m', criado_em) = ?" if mes else ""
    params_count = [owner_email] + ([mes] if mes else [])
    params_rows  = [owner_email] + ([mes] if mes else []) + [limit, offset]
    with get_conn() as conn:
        _ensure_api_uso_cols(conn)
        total = conn.execute(
            f"SELECT COUNT(*) FROM api_uso WHERE owner_email = ? {mes_filter}",
            params_count,
        ).fetchone()[0]
        rows = conn.execute(
            f"""SELECT id, criado_em, tipo, modelo, paciente_nome,
                       input_tokens, output_tokens, custo_usd,
                       CASE WHEN sec_email IS NULL THEN 'fisio' ELSE 'secretaria' END AS origem,
                       sec_email, owner_email
                FROM api_uso
               WHERE owner_email = ? {mes_filter}
               ORDER BY criado_em DESC
               LIMIT ? OFFSET ?""",
            params_rows,
        ).fetchall()
    return {"total": total, "items": [_row_to_dict(r) for r in rows]}


# ---------- Créditos / Recarga ----------

def _init_recarga_table():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recarga (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_email TEXT    NOT NULL,
                valor_brl   REAL    NOT NULL,
                descricao   TEXT,
                criado_em   TEXT    NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recarga_owner ON recarga(owner_email)")
        conn.commit()


def registrar_recarga(owner_email: str, valor_brl: float, descricao: str | None = None) -> dict:
    _init_recarga_table()
    now = _now()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO recarga (owner_email, valor_brl, descricao, criado_em) VALUES (?, ?, ?, ?)",
            (owner_email, valor_brl, descricao, now),
        )
        conn.commit()
        return {"id": cur.lastrowid, "owner_email": owner_email, "valor_brl": valor_brl,
                "descricao": descricao, "criado_em": now}


def get_creditos(owner_email: str, cotacao_usd_brl: float) -> dict:
    """Retorna saldo de créditos do fisio: total carregado - total gasto (em BRL)."""
    _init_recarga_table()
    with get_conn() as conn:
        row_rec = conn.execute(
            "SELECT COALESCE(SUM(valor_brl), 0) as total FROM recarga WHERE owner_email = ?",
            (owner_email,),
        ).fetchone()
        total_carregado = float(row_rec["total"] or 0)

        row_uso = conn.execute(
            "SELECT COALESCE(SUM(custo_usd), 0) as total FROM api_uso WHERE owner_email = ?",
            (owner_email,),
        ).fetchone()
        total_gasto_usd = float(row_uso["total"] or 0)
        total_gasto_brl = round(total_gasto_usd * cotacao_usd_brl, 2)

        saldo = round(total_carregado - total_gasto_brl, 2)
        pct_restante = round((saldo / total_carregado * 100), 1) if total_carregado > 0 else 0.0

        historico = conn.execute(
            "SELECT * FROM recarga WHERE owner_email = ? ORDER BY criado_em DESC LIMIT 50",
            (owner_email,),
        ).fetchall()

        return {
            "total_carregado_brl": round(total_carregado, 2),
            "total_gasto_brl": total_gasto_brl,
            "saldo_brl": saldo,
            "pct_restante": pct_restante,
            "cotacao_usd_brl": cotacao_usd_brl,
            "historico": [_row_to_dict(r) for r in historico],
        }


# ---------- Pagamento PIX (Mercado Pago) ----------

def _init_pagamento_pix_table():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pagamento_pix (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_email   TEXT    NOT NULL,
                payment_id    TEXT    NOT NULL UNIQUE,
                creditos      INTEGER NOT NULL,
                valor_brl     REAL    NOT NULL,
                status        TEXT    NOT NULL DEFAULT 'pending',
                creditado     INTEGER NOT NULL DEFAULT 0,
                qr_code       TEXT,
                expira_em     TEXT,
                criado_em     TEXT    NOT NULL,
                atualizado_em TEXT    NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pix_owner   ON pagamento_pix(owner_email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pix_payment ON pagamento_pix(payment_id)")
        conn.commit()


def criar_pagamento_pix(owner_email: str, payment_id: str, creditos: int,
                        valor_brl: float, qr_code: str, expira_em: str) -> dict:
    _init_pagamento_pix_table()
    now = _now()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO pagamento_pix
                (owner_email, payment_id, creditos, valor_brl, status, creditado,
                 qr_code, expira_em, criado_em, atualizado_em)
            VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?)
        """, (owner_email, payment_id, creditos, valor_brl, qr_code, expira_em, now, now))
        conn.commit()
    return get_pagamento_pix(payment_id)


def get_pagamento_pix(payment_id: str) -> dict | None:
    _init_pagamento_pix_table()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pagamento_pix WHERE payment_id = ?", (payment_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_pagamento_pix_por_owner(payment_id: str, owner_email: str) -> dict | None:
    _init_pagamento_pix_table()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pagamento_pix WHERE payment_id = ? AND owner_email = ?",
            (payment_id, owner_email)
        ).fetchone()
    return _row_to_dict(row) if row else None


def aprovar_pagamento_pix(payment_id: str) -> bool:
    """Marca aprovado e registra crédito. Retorna False se já creditado (idempotência)."""
    _init_pagamento_pix_table()
    now = _now()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pagamento_pix WHERE payment_id = ? AND creditado = 0",
            (payment_id,)
        ).fetchone()
        if not row:
            return False
        p = _row_to_dict(row)
        conn.execute(
            "UPDATE pagamento_pix SET status='approved', creditado=1, atualizado_em=? WHERE payment_id=?",
            (now, payment_id)
        )
        conn.commit()
    registrar_recarga(p["owner_email"], p["valor_brl"],
                      descricao=f"PIX Mercado Pago — {p['creditos']} créditos")
    return True


def atualizar_status_pagamento_pix(payment_id: str, status: str):
    _init_pagamento_pix_table()
    with get_conn() as conn:
        conn.execute(
            "UPDATE pagamento_pix SET status=?, atualizado_em=? WHERE payment_id=?",
            (status, _now(), payment_id)
        )
        conn.commit()


# ---------- Documentos ----------

def salvar_documento(paciente_id: int, nome_original: str, caminho: str, resumo_ia: str | None) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO documento (paciente_id, nome_original, caminho, resumo_ia, criado_em) VALUES (?, ?, ?, ?, ?)",
            (paciente_id, nome_original, caminho, resumo_ia, _now()),
        )
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM documento WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_documentos_paciente(paciente_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM documento WHERE paciente_id = ? AND deletado_em IS NULL ORDER BY criado_em DESC",
            (paciente_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_documento(doc_id: int) -> dict | None:
    with get_conn() as conn:
        return _row_to_dict(conn.execute("SELECT * FROM documento WHERE id = ?", (doc_id,)).fetchone())


def deletar_documento(doc_id: int):
    """Soft delete de documento — mantém registro no banco."""
    with get_conn() as conn:
        conn.execute("UPDATE documento SET deletado_em = ? WHERE id = ?", (_now(), doc_id))
        conn.commit()


# ---------- Pacotes ----------

def criar_pacote(paciente_id: int, total_sessoes: int, pago: bool, valor_pago: float | None, data_pagamento: str | None, descricao: str | None) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT id FROM pacote WHERE paciente_id = ? AND deletado_em IS NULL
               AND sessoes_usadas < total_sessoes ORDER BY criado_em DESC LIMIT 1""",
            (paciente_id,),
        ).fetchone()
        if row:
            raise ValueError("Paciente já possui um pacote ativo. Aguarde o término ou remova-o do histórico.")

        cur = conn.execute(
            "INSERT INTO pacote (paciente_id, total_sessoes, pago, valor_pago, data_pagamento, descricao, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (paciente_id, total_sessoes, 1 if pago else 0, valor_pago, data_pagamento, descricao, _now()),
        )
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM pacote WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_pacotes_paciente(paciente_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT pk.*, p.nome AS paciente_nome
            FROM pacote pk
            JOIN paciente p ON p.id = pk.paciente_id
            WHERE pk.paciente_id = ? AND pk.deletado_em IS NULL
            ORDER BY pk.criado_em DESC
        """, (paciente_id,)).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_pacote_ativo(paciente_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM pacote WHERE paciente_id = ? AND deletado_em IS NULL
               AND sessoes_usadas < total_sessoes ORDER BY criado_em DESC LIMIT 1""",
            (paciente_id,),
        ).fetchone()
        return _row_to_dict(row)


def deletar_pacote(pacote_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE pacote SET deletado_em = ? WHERE id = ?", (_now(), pacote_id))
        conn.commit()


# ---------- Procedimentos Extras ----------

def adicionar_procedimento(sessao_id: int, paciente_id: int, descricao: str, valor: float | None, data: str | None) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            _SQL_INSERT_PROCEDIMENTO_EXTRA,
            (sessao_id, paciente_id, descricao, valor, data, _now()),
        )
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM procedimento_extra WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_procedimentos_sessao(sessao_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM procedimento_extra WHERE sessao_id = ? AND deletado_em IS NULL ORDER BY criado_em",
            (sessao_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def atualizar_procedimento(proc_id: int, descricao: str, valor):
    with get_conn() as conn:
        conn.execute(
            "UPDATE procedimento_extra SET descricao = ?, valor = ? WHERE id = ? AND deletado_em IS NULL",
            (descricao, valor, proc_id),
        )
        conn.commit()


def deletar_procedimento(proc_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE procedimento_extra SET deletado_em = ? WHERE id = ?", (_now(), proc_id))
        conn.commit()


# ---------- Faturamento de Pacientes ----------

def get_faturamento_pacientes(ano_mes: str | None = None, paciente_id: int | None = None, owner_email: str | None = None) -> dict:
    """
    Retorna pacotes e procedimentos extras, filtrado por mês e/ou paciente.
    ano_mes: 'YYYY-MM'. Para pacotes usa data_pagamento; para procedimentos usa data.
    """
    with get_conn() as conn:
        # ---- Pacotes ----
        pk_where = ["pk.deletado_em IS NULL", "pk.valor_pago IS NOT NULL"]
        pk_params: list = []
        if ano_mes:
            pk_where.append("strftime('%Y-%m', COALESCE(pk.data_pagamento, DATE(pk.criado_em))) = ?")
            pk_params.append(ano_mes)
        if paciente_id:
            pk_where.append("pk.paciente_id = ?")
            pk_params.append(paciente_id)
        if owner_email:
            pk_where.append(_SQL_OWNER_EMAIL_FILTER)
            pk_params.append(owner_email)

        pacotes = [_row_to_dict(r) for r in conn.execute(f"""
            SELECT pk.id, pk.paciente_id, p.nome AS paciente_nome,
                   pk.total_sessoes, pk.sessoes_usadas, pk.valor_pago,
                   COALESCE(pk.data_pagamento, DATE(pk.criado_em)) AS data_pagamento,
                   pk.descricao, pk.criado_em,
                   CASE WHEN pk.sessoes_usadas < pk.total_sessoes THEN 'ativo' ELSE 'esgotado' END AS status
            FROM pacote pk
            JOIN paciente p ON p.id = pk.paciente_id
            WHERE {" AND ".join(pk_where)}
            ORDER BY pk.data_pagamento DESC, pk.criado_em DESC
        """, pk_params).fetchall()]

        # ---- Procedimentos extras ----
        pe_where = ["pe.deletado_em IS NULL", "pe.valor IS NOT NULL"]
        pe_params: list = []
        if ano_mes:
            pe_where.append("strftime('%Y-%m', COALESCE(pe.data, pe.criado_em)) = ?")
            pe_params.append(ano_mes)
        if paciente_id:
            pe_where.append("pe.paciente_id = ?")
            pe_params.append(paciente_id)
        if owner_email:
            pe_where.append(_SQL_OWNER_EMAIL_FILTER)
            pe_params.append(owner_email)

        procedimentos = [_row_to_dict(r) for r in conn.execute(f"""
            SELECT pe.id, pe.sessao_id, pe.paciente_id, p.nome AS paciente_nome,
                   pe.descricao, pe.valor,
                   COALESCE(pe.data, DATE(pe.criado_em)) AS data,
                   pe.criado_em
            FROM procedimento_extra pe
            JOIN paciente p ON p.id = pe.paciente_id
            WHERE {" AND ".join(pe_where)}
            ORDER BY pe.data DESC, pe.criado_em DESC
        """, pe_params).fetchall()]

        total_pacotes = sum(p["valor_pago"] or 0 for p in pacotes)
        total_procedimentos = sum(p["valor"] or 0 for p in procedimentos)
        total_recebido = round(total_pacotes + total_procedimentos, 2)
        total_sessoes_vendidas = sum(p["total_sessoes"] or 0 for p in pacotes)

        # meses disponíveis (união de datas de pacotes e procedimentos — filtrado por owner)
        owner_join_pk = "JOIN paciente p ON p.id = pacote.paciente_id AND p.owner_email = ?" if owner_email else ""
        owner_join_pe = "JOIN paciente p ON p.id = procedimento_extra.paciente_id AND p.owner_email = ?" if owner_email else ""
        meses_params = ([owner_email, owner_email] if owner_email else [])
        meses_rows = conn.execute(f"""
            SELECT DISTINCT mes FROM (
                SELECT strftime('%Y-%m', COALESCE(pacote.data_pagamento, DATE(pacote.criado_em))) AS mes
                FROM pacote {owner_join_pk} WHERE pacote.deletado_em IS NULL AND pacote.valor_pago IS NOT NULL
                UNION
                SELECT strftime('%Y-%m', COALESCE(procedimento_extra.data, procedimento_extra.criado_em)) AS mes
                FROM procedimento_extra {owner_join_pe} WHERE procedimento_extra.deletado_em IS NULL AND procedimento_extra.valor IS NOT NULL
            ) ORDER BY mes DESC
        """, meses_params).fetchall()

        # pacientes disponíveis (união de pacotes e procedimentos — filtrado por owner)
        owner_pac_filter = "AND p.owner_email = ?" if owner_email else ""
        pacientes_rows = conn.execute(f"""
            SELECT DISTINCT p.id, p.nome FROM paciente p
            WHERE p.deletado_em IS NULL {owner_pac_filter} AND (
                EXISTS (SELECT 1 FROM pacote pk WHERE pk.paciente_id = p.id AND pk.deletado_em IS NULL AND pk.valor_pago IS NOT NULL)
                OR
                EXISTS (SELECT 1 FROM procedimento_extra pe WHERE pe.paciente_id = p.id AND pe.deletado_em IS NULL AND pe.valor IS NOT NULL)
            )
            ORDER BY p.nome COLLATE NOCASE
        """, ([owner_email] if owner_email else [])).fetchall()

        return {
            "pacotes": pacotes,
            "procedimentos": procedimentos,
            "total_recebido": total_recebido,
            "total_pacotes_valor": round(total_pacotes, 2),
            "total_procedimentos_valor": round(total_procedimentos, 2),
            "qtd_pacotes": len(pacotes),
            "total_sessoes_vendidas": total_sessoes_vendidas,
            "qtd_procedimentos": len(procedimentos),
            "meses_disponiveis": [r["mes"] for r in meses_rows if r["mes"]],
            "pacientes_disponiveis": [_row_to_dict(r) for r in pacientes_rows],
        }


# ---------- Notas Fiscais ----------

def _proximo_numero_nf(conn) -> str:
    row = conn.execute("SELECT COUNT(*) as total FROM nota_fiscal").fetchone()
    return str((row["total"] or 0) + 1).zfill(7)


def emitir_nota_fiscal(paciente_id: int | None, paciente_nome: str, valor_servico: float,
                       descricao: str, competencia: str | None, dados_json: str, owner_email: str | None = None) -> dict:
    import secrets as _secrets
    now = _now()
    from datetime import datetime, timezone as tz
    data_emissao = datetime.now(tz.utc).strftime("%d/%m/%Y %H:%M:%S")
    codigo = _secrets.token_hex(4).upper()

    with get_conn() as conn:
        numero = _proximo_numero_nf(conn)
        cur = conn.execute(
            """INSERT INTO nota_fiscal
               (numero, paciente_id, paciente_nome, valor_servico, descricao,
                competencia, data_emissao, codigo_verificacao, dados_json, owner_email, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (numero, paciente_id, paciente_nome, valor_servico, descricao,
             competencia, data_emissao, codigo, dados_json, owner_email, now),
        )
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM nota_fiscal WHERE id = ?", (cur.lastrowid,)).fetchone())


def listar_notas_fiscais(
    q: str | None = None,
    paciente_id: int | None = None,
    competencia: str | None = None,
    owner_email: str | None = None,
) -> list[dict]:
    with get_conn() as conn:
        where_parts = ["deletado_em IS NULL"]
        params: list = []
        if owner_email:
            where_parts.append("owner_email = ?")
            params.append(owner_email)
        if paciente_id:
            where_parts.append("paciente_id = ?")
            params.append(paciente_id)
        if competencia:
            where_parts.append("competencia = ?")
            params.append(competencia)
        if q:
            where_parts.append("(paciente_nome LIKE ? OR numero LIKE ? OR descricao LIKE ?)")
            like = f"%{q}%"
            params += [like, like, like]
        where = " AND ".join(where_parts)
        rows = conn.execute(
            f"SELECT * FROM nota_fiscal WHERE {where} ORDER BY criado_em DESC",
            params,
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_nota_fiscal(nf_id: int) -> dict | None:
    with get_conn() as conn:
        return _row_to_dict(conn.execute("SELECT * FROM nota_fiscal WHERE id = ?", (nf_id,)).fetchone())


def cancelar_nota_fiscal(nf_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE nota_fiscal SET status = 'cancelada' WHERE id = ?", (nf_id,))
        conn.commit()


# ---------- WebAuthn ----------

def get_usuario_por_username(username: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM usuario WHERE username = ?", (username,)).fetchone()
        return _row_to_dict(row)


def criar_usuario(username: str) -> dict:
    import uuid
    uid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO usuario (id, username, criado_em) VALUES (?, ?, ?)",
            (uid, username, _now()),
        )
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM usuario WHERE id = ?", (uid,)).fetchone())


def salvar_credencial_webauthn(usuario_id: str, credential_id: str, public_key: bytes, sign_count: int) -> dict:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO webauthn_credential (id, usuario_id, public_key, sign_count, criado_em) VALUES (?, ?, ?, ?, ?)",
            (credential_id, usuario_id, public_key, sign_count, _now()),
        )
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM webauthn_credential WHERE id = ?", (credential_id,)).fetchone())


def get_credencial_webauthn(usuario_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM webauthn_credential WHERE usuario_id = ? ORDER BY criado_em DESC LIMIT 1",
            (usuario_id,),
        ).fetchone()
        return _row_to_dict(row)


def atualizar_sign_count(credential_id: str, sign_count: int):
    with get_conn() as conn:
        conn.execute("UPDATE webauthn_credential SET sign_count = ? WHERE id = ?", (sign_count, credential_id))
        conn.commit()


# ── Usuários (Google SSO) ──────────────────────────────────────────────────────

def _init_usuario_table():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usuario_google (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                email     TEXT    UNIQUE NOT NULL,
                nome      TEXT,
                foto_url  TEXT,
                ativo     INTEGER NOT NULL DEFAULT 1,
                criado_em TEXT    NOT NULL
            )
        """)
        conn.commit()


def upsert_usuario(email: str, nome: str, foto_url: str | None, admin_email: str = "") -> dict:
    """Cria ou atualiza o usuário pelo e-mail. Retorna o registro atualizado.
    - Admin entra com ativo=1 automaticamente.
    - Primeiro usuário assume pacientes órfãos e entra com ativo=1.
    - Demais novos usuários entram com ativo=0 (pendente de aprovação)."""
    _init_usuario_table()
    agora = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        is_new = conn.execute("SELECT COUNT(*) FROM usuario_google WHERE email = ?", (email,)).fetchone()[0] == 0
        is_first = conn.execute("SELECT COUNT(*) FROM usuario_google").fetchone()[0] == 0
        is_admin = email == admin_email
        ativo = 1 if (is_admin or is_first) else 0
        conn.execute("""
            INSERT INTO usuario_google (email, nome, foto_url, ativo, criado_em)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET nome = excluded.nome, foto_url = excluded.foto_url
        """, (email, nome, foto_url, ativo, agora))
        if is_new and is_first:
            conn.execute("UPDATE paciente SET owner_email = ? WHERE owner_email IS NULL", (email,))
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM usuario_google WHERE email = ?", (email,)).fetchone())


def listar_usuarios() -> list[dict]:
    _init_usuario_table()
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM usuario_google ORDER BY ativo ASC, criado_em DESC").fetchall()
        return [_row_to_dict(r) for r in rows]


def aprovar_usuario(email: str):
    _init_usuario_table()
    with get_conn() as conn:
        conn.execute("UPDATE usuario_google SET ativo = 1 WHERE email = ?", (email,))
        conn.commit()


def revogar_usuario(email: str):
    """Remove o fisioterapeuta do sistema (hard delete).
    O audit_log preserva o histórico. Na próxima solicitação de acesso,
    um novo registro será criado com ativo=0 (pendente de aprovação)."""
    _init_usuario_table()
    agora = _now()
    with get_conn() as conn:
        # Encerra vínculos ativos de secretaria vinculados a este fisio
        conn.execute(
            "UPDATE secretaria_link SET deletado_em = ? WHERE fisio_email = ? AND deletado_em IS NULL",
            (agora, email),
        )
        # Remove o registro do fisio — abre porta para nova solicitação futura
        conn.execute("DELETE FROM usuario_google WHERE email = ?", (email,))
        conn.commit()


def get_nome_fisio(email: str) -> str | None:
    """Retorna o nome do fisioterapeuta pelo e-mail, ou None se não encontrado."""
    _init_usuario_table()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT nome FROM usuario_google WHERE email = ?",
            (email.lower().strip(),)
        ).fetchone()
        return row["nome"] if row else None


def email_existe_como_fisio(email: str) -> bool:
    """Retorna True se o e-mail já tem conta de fisioterapeuta cadastrada (ativa ou pendente)."""
    _init_usuario_table()
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM usuario_google WHERE email = ?",
            (email.lower().strip(),)
        ).fetchone()[0] > 0


def email_e_fisio_ativo(email: str) -> bool:
    """Retorna True apenas se o e-mail está aprovado como fisioterapeuta (ativo=1).
    Fisio pendente (ativo=0) NÃO conta — permite que o e-mail faça login como secretaria."""
    _init_usuario_table()
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM usuario_google WHERE email = ? AND ativo = 1",
            (email.lower().strip(),)
        ).fetchone()[0] > 0


# ---------- Secretaria ----------

def _init_secretaria_table():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS secretaria_link (
                secretaria_email TEXT PRIMARY KEY,
                fisio_email      TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'ativa',
                criado_em        TEXT NOT NULL,
                deletado_em      TEXT
            )
        """)
        # Migrações: adiciona colunas se ainda não existirem
        cols = [r[1] for r in conn.execute("PRAGMA table_info(secretaria_link)").fetchall()]
        if "status" not in cols:
            conn.execute("ALTER TABLE secretaria_link ADD COLUMN status TEXT NOT NULL DEFAULT 'ativa'")
        if "deletado_em" not in cols:
            conn.execute("ALTER TABLE secretaria_link ADD COLUMN deletado_em TEXT")
        conn.commit()


def convidar_secretaria(secretaria_email: str, fisio_email: str):
    """Fisio cria convite com status=pendente. Admin deve aprovar."""
    _init_secretaria_table()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO secretaria_link (secretaria_email, fisio_email, status, criado_em, deletado_em)
            VALUES (?, ?, 'pendente', ?, NULL)
            ON CONFLICT(secretaria_email) DO UPDATE SET
                fisio_email = excluded.fisio_email,
                status      = 'pendente',
                criado_em   = excluded.criado_em,
                deletado_em = NULL
        """, (secretaria_email.lower().strip(), fisio_email.lower().strip(),
              datetime.now(timezone.utc).isoformat()))
        conn.commit()


def vincular_secretaria(secretaria_email: str, fisio_email: str):
    """Mantido por compatibilidade — usa fluxo de convite pendente."""
    convidar_secretaria(secretaria_email, fisio_email)


def aprovar_secretaria(secretaria_email: str):
    """Admin aprova o convite — status passa para 'ativa'."""
    _init_secretaria_table()
    with get_conn() as conn:
        conn.execute(
            "UPDATE secretaria_link SET status = 'ativa', deletado_em = NULL WHERE secretaria_email = ?",
            (secretaria_email.lower().strip(),)
        )
        conn.commit()


def rejeitar_secretaria(secretaria_email: str):
    """Admin rejeita o convite — exclusão lógica."""
    _init_secretaria_table()
    with get_conn() as conn:
        conn.execute(
            "UPDATE secretaria_link SET deletado_em = ? WHERE secretaria_email = ?",
            (datetime.now(timezone.utc).isoformat(), secretaria_email.lower().strip())
        )
        conn.commit()


def get_fisio_da_secretaria(secretaria_email: str) -> str | None:
    """Retorna fisio_email somente se o vínculo estiver ativo."""
    _init_secretaria_table()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT fisio_email FROM secretaria_link WHERE secretaria_email = ? AND status = 'ativa' AND deletado_em IS NULL",
            (secretaria_email.lower().strip(),)
        ).fetchone()
        return row[0] if row else None


def get_status_secretaria(secretaria_email: str) -> str | None:
    """Retorna o status do convite ('pendente', 'ativa') ou None se não existe/deletado."""
    _init_secretaria_table()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM secretaria_link WHERE secretaria_email = ? AND deletado_em IS NULL",
            (secretaria_email.lower().strip(),)
        ).fetchone()
        return row[0] if row else None


def desvincular_secretaria(secretaria_email: str):
    """Fisio remove vínculo da secretaria — exclusão lógica."""
    _init_secretaria_table()
    with get_conn() as conn:
        conn.execute(
            "UPDATE secretaria_link SET deletado_em = ? WHERE secretaria_email = ?",
            (datetime.now(timezone.utc).isoformat(), secretaria_email.lower().strip())
        )
        conn.commit()


def get_secretaria_do_fisio(fisio_email: str) -> dict | None:
    """Retorna o registro da secretaria vinculada ao fisio (não deletada), ou None."""
    if not fisio_email:
        return None
    _init_secretaria_table()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM secretaria_link WHERE fisio_email = ? AND deletado_em IS NULL",
            (fisio_email.lower().strip(),)
        ).fetchone()
        return _row_to_dict(row) if row else None


def listar_convites_secretaria_pendentes() -> list[dict]:
    """Lista todos os convites ativos com status=pendente (para o admin)."""
    _init_secretaria_table()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM secretaria_link WHERE status = 'pendente' AND deletado_em IS NULL ORDER BY criado_em DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def listar_todos_links_secretaria() -> list[dict]:
    """Lista todos os vínculos não deletados (pendentes e ativos) para o admin."""
    _init_secretaria_table()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM secretaria_link WHERE deletado_em IS NULL ORDER BY criado_em DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ---------- Configurações do usuário ----------

VALOR_SESSAO_AVULSA_PADRAO = 280.0


def _migrar_criptografar_pii():
    """Criptografa CPFs e endereços existentes em plaintext e popula cpf_hash. Executa apenas se FIELD_ENCRYPTION_KEY configurada."""
    if not _get_fernet():
        return
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, cpf, cpf_hash, endereco FROM paciente WHERE deletado_em IS NULL"
        ).fetchall()
        atualizados = 0
        for row in rows:
            cpf_raw = row["cpf"]
            end_raw = row["endereco"]
            hash_atual = row["cpf_hash"]

            # Criptografa CPF se ainda em plaintext
            novo_cpf = _encrypt_field(cpf_raw) if cpf_raw and not cpf_raw.startswith(_ENC_PREFIX) else cpf_raw

            # Reconstrói cpf_hash: decripta para obter valor limpo, aplica HMAC
            cpf_limpo = _decrypt_field(novo_cpf) if novo_cpf else None
            novo_hash = _cpf_hash(cpf_limpo) if cpf_limpo else None

            novo_end = _encrypt_field(end_raw) if end_raw and not end_raw.startswith(_ENC_PREFIX) else end_raw

            if novo_cpf != cpf_raw or novo_end != end_raw or novo_hash != hash_atual:
                conn.execute(
                    "UPDATE paciente SET cpf = ?, cpf_hash = ?, endereco = ? WHERE id = ?",
                    (novo_cpf, novo_hash, novo_end, row["id"]),
                )
                atualizados += 1
        if atualizados:
            conn.commit()
            logger.info("_migrar_criptografar_pii: %d paciente(s) com PII criptografado/hash populado", atualizados)


def _migrar_config_usuario():
    """Garante que usuario_google tem as colunas de configuração."""
    _init_usuario_table()
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(usuario_google)").fetchall()]
        if "valor_sessao_avulsa" not in cols:
            conn.execute("ALTER TABLE usuario_google ADD COLUMN valor_sessao_avulsa REAL")
        if "cobrar_avulsa" not in cols:
            conn.execute("ALTER TABLE usuario_google ADD COLUMN cobrar_avulsa INTEGER NOT NULL DEFAULT 1")
        if "google_refresh_token" not in cols:
            conn.execute("ALTER TABLE usuario_google ADD COLUMN google_refresh_token TEXT")
        conn.execute(
            "UPDATE usuario_google SET valor_sessao_avulsa = ? WHERE valor_sessao_avulsa IS NULL",
            (VALOR_SESSAO_AVULSA_PADRAO,),
        )
        conn.commit()


def salvar_google_refresh_token(email: str, refresh_token: str) -> None:
    """Persiste o refresh token do Google Calendar para o usuário."""
    _migrar_config_usuario()
    with get_conn() as conn:
        conn.execute(
            "UPDATE usuario_google SET google_refresh_token = ? WHERE email = ?",
            (refresh_token, email),
        )
        conn.commit()


def get_google_refresh_token(email: str) -> str | None:
    """Retorna o refresh token do Google Calendar do usuário, ou None se não houver."""
    _migrar_config_usuario()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT google_refresh_token FROM usuario_google WHERE email = ?", (email,)
        ).fetchone()
        return row["google_refresh_token"] if row else None


def get_config_usuario(owner_email: str) -> dict:
    _migrar_config_usuario()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT valor_sessao_avulsa, cobrar_avulsa FROM usuario_google WHERE email = ?", (owner_email,)
        ).fetchone()
        valor = (row["valor_sessao_avulsa"] if row else None) or VALOR_SESSAO_AVULSA_PADRAO
        cobrar = bool(row["cobrar_avulsa"]) if row and row["cobrar_avulsa"] is not None else True
        return {"valor_sessao_avulsa": valor, "cobrar_avulsa": cobrar}


def set_config_usuario(owner_email: str, valor_sessao_avulsa: float | None, cobrar_avulsa: bool = True):
    _migrar_config_usuario()
    with get_conn() as conn:
        conn.execute(
            "UPDATE usuario_google SET valor_sessao_avulsa = ?, cobrar_avulsa = ? WHERE email = ?",
            (valor_sessao_avulsa, 1 if cobrar_avulsa else 0, owner_email),
        )
        conn.commit()


# ---------- Push Subscriptions ----------

def _init_push_table():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_subscription (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_email      TEXT    NOT NULL,
                endpoint         TEXT    NOT NULL UNIQUE,
                subscription_json TEXT   NOT NULL,
                criado_em        TEXT    NOT NULL
            )
        """)
        conn.commit()


def salvar_subscription(owner_email: str, subscription_json: str):
    """Upsert: cria ou atualiza subscription pelo endpoint."""
    _init_push_table()
    import json as _json
    endpoint = _json.loads(subscription_json).get("endpoint", "")
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO push_subscription (owner_email, endpoint, subscription_json, criado_em)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET subscription_json = excluded.subscription_json, owner_email = excluded.owner_email
        """, (owner_email, endpoint, subscription_json, _now()))
        conn.commit()


def remover_subscription_por_endpoint(endpoint: str):
    _init_push_table()
    with get_conn() as conn:
        conn.execute("DELETE FROM push_subscription WHERE endpoint = ?", (endpoint,))
        conn.commit()


def get_subscriptions_por_owner(owner_email: str) -> list[dict]:
    _init_push_table()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM push_subscription WHERE owner_email = ?", (owner_email,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ---------- Queries para notificações agendadas ----------

def get_sessoes_abertas_por_owner() -> dict[str, list[str]]:
    """Retorna {owner_email: [nomes dos pacientes com sessão aberta hoje]}."""
    hoje = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT p.owner_email, p.nome
            FROM sessao s
            JOIN paciente p ON p.id = s.paciente_id
            WHERE s.status = 'aberta' AND s.deletado_em IS NULL
              AND p.deletado_em IS NULL AND p.owner_email IS NOT NULL
              AND DATE(s.criado_em) = ?
        """, (hoje,)).fetchall()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r["owner_email"], []).append(r["nome"])
    return result


def get_aniversariantes_hoje_por_owner() -> dict[str, list[str]]:
    """Retorna {owner_email: [nomes de pacientes que fazem aniversário hoje]}."""
    hoje = date.today().strftime("%m-%d")
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT owner_email, nome
            FROM paciente
            WHERE deletado_em IS NULL AND owner_email IS NOT NULL
              AND data_nascimento IS NOT NULL
              AND strftime('%m-%d', data_nascimento) = ?
        """, (hoje,)).fetchall()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r["owner_email"], []).append(r["nome"])
    return result


def get_pacientes_sem_sessao_recente_por_owner(dias: int = 30) -> dict[str, list[str]]:
    """Retorna {owner_email: [nomes de pacientes sem sessão há X dias]}."""
    corte = (date.today() - timedelta(days=dias)).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT p.owner_email, p.nome
            FROM paciente p
            WHERE p.deletado_em IS NULL AND p.owner_email IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM sessao s
                WHERE s.paciente_id = p.id AND s.deletado_em IS NULL
                  AND DATE(s.criado_em) >= ?
              )
              AND EXISTS (
                SELECT 1 FROM sessao s2
                WHERE s2.paciente_id = p.id AND s2.deletado_em IS NULL
              )
        """, (corte,)).fetchall()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r["owner_email"], []).append(r["nome"])
    return result


def get_resumo_semana_por_owner() -> dict[str, dict]:
    """Retorna {owner_email: {sessoes, pacientes}} da semana passada."""
    fim   = (date.today() - timedelta(days=date.today().weekday() + 1)).isoformat()
    inicio = (date.today() - timedelta(days=date.today().weekday() + 7)).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT p.owner_email,
                   COUNT(s.id) AS sessoes,
                   COUNT(DISTINCT s.paciente_id) AS pacientes
            FROM sessao s
            JOIN paciente p ON p.id = s.paciente_id
            WHERE s.deletado_em IS NULL AND p.deletado_em IS NULL
              AND p.owner_email IS NOT NULL
              AND DATE(s.criado_em) BETWEEN ? AND ?
              AND s.status = 'encerrada'
            GROUP BY p.owner_email
        """, (inicio, fim)).fetchall()
    return {r["owner_email"]: {"sessoes": r["sessoes"], "pacientes": r["pacientes"]} for r in rows}


def get_pacotes_vencidos_sem_renovar_por_owner(dias: int = 7) -> dict[str, list[str]]:
    """Retorna {owner_email: [nomes de pacientes com pacote esgotado há X dias sem renovar]}."""
    corte = (date.today() - timedelta(days=dias)).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT p.owner_email, p.nome
            FROM paciente p
            WHERE p.deletado_em IS NULL AND p.owner_email IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM pacote pk
                WHERE pk.paciente_id = p.id AND pk.deletado_em IS NULL
                  AND pk.sessoes_usadas >= pk.total_sessoes
                  AND DATE(pk.criado_em) <= ?
              )
              AND NOT EXISTS (
                SELECT 1 FROM pacote pk2
                WHERE pk2.paciente_id = p.id AND pk2.deletado_em IS NULL
                  AND pk2.sessoes_usadas < pk2.total_sessoes
              )
        """, (corte,)).fetchall()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r["owner_email"], []).append(r["nome"])
    return result


def get_sessoes_restantes_paciente(paciente_id: int) -> int | None:
    """Retorna sessões restantes do pacote ativo, ou None se não há pacote."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT (total_sessoes - sessoes_usadas) AS restantes
            FROM pacote
            WHERE paciente_id = ? AND deletado_em IS NULL
              AND sessoes_usadas < total_sessoes
            ORDER BY criado_em DESC LIMIT 1
        """, (paciente_id,)).fetchone()
        return row["restantes"] if row else None


def listar_todos_pacotes_ativos(owner_email: str) -> list[dict]:
    """Retorna todos os pacotes ativos de todos os pacientes de um fisioterapeuta."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT pk.*, p.nome AS paciente_nome
            FROM pacote pk
            JOIN paciente p ON p.id = pk.paciente_id
            WHERE p.owner_email = ? AND pk.deletado_em IS NULL
              AND pk.sessoes_usadas < pk.total_sessoes
            ORDER BY pk.criado_em DESC
        """, (owner_email,)).fetchall()
        return [_row_to_dict(r) for r in rows]


def atualizar_pagamento_pacote(pacote_id: int, pago: bool):
    """Atualiza o status de pagamento de um pacote."""
    with get_conn() as conn:
        conn.execute("UPDATE pacote SET pago = ? WHERE id = ?", (pago, pacote_id))
        conn.commit()


# ---------- Config Precificação ----------

def _init_config_precificacao_table():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS config_precificacao (
                id           INTEGER PRIMARY KEY CHECK (id = 1),
                margem_pct   REAL NOT NULL DEFAULT 100.0,
                imposto_pct  REAL NOT NULL DEFAULT 14.0,
                atualizado_em TEXT NOT NULL
            )
        """)
        conn.commit()


def get_config_precificacao() -> dict:
    _init_config_precificacao_table()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM config_precificacao WHERE id = 1").fetchone()
    if row:
        return _row_to_dict(row)
    return {"margem_pct": 100.0, "imposto_pct": 14.0}


def salvar_config_precificacao(margem_pct: float, imposto_pct: float) -> dict:
    _init_config_precificacao_table()
    now = _now()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO config_precificacao (id, margem_pct, imposto_pct, atualizado_em)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                margem_pct    = excluded.margem_pct,
                imposto_pct   = excluded.imposto_pct,
                atualizado_em = excluded.atualizado_em
        """, (margem_pct, imposto_pct, now))
        conn.commit()
    return {"margem_pct": margem_pct, "imposto_pct": imposto_pct, "atualizado_em": now}


def get_custo_medio_mensal_usd() -> dict:
    """Retorna custo médio mensal por usuário ativo nos últimos 3 meses completos.
    Retorna {custo_medio_usd, usuarios_ativos, meses_analisados, tem_dados}."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                strftime('%Y-%m', criado_em) AS mes,
                owner_email,
                SUM(custo_usd) AS custo_mes
            FROM api_uso
            WHERE owner_email IS NOT NULL
              AND criado_em >= date('now', '-3 months')
            GROUP BY mes, owner_email
        """).fetchall()

    if not rows:
        return {"custo_medio_usd": 0.50, "usuarios_ativos": 0, "meses_analisados": 0, "tem_dados": False}

    from collections import defaultdict
    por_mes: dict = defaultdict(float)
    usuarios_por_mes: dict = defaultdict(set)
    for r in rows:
        mes, email, custo = r[0], r[1], r[2] or 0.0
        por_mes[mes] += custo
        usuarios_por_mes[mes].add(email)

    medias = []
    for mes, total in por_mes.items():
        n = len(usuarios_por_mes[mes])
        if n > 0:
            medias.append(total / n)

    custo_medio = sum(medias) / len(medias) if medias else 0.50
    usuarios_ativos = len({r[1] for r in rows})

    return {
        "custo_medio_usd": round(custo_medio, 6),
        "usuarios_ativos": usuarios_ativos,
        "meses_analisados": len(por_mes),
        "tem_dados": True,
    }

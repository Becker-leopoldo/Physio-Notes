import sqlite3
import os
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH") or os.path.join(os.path.dirname(__file__), "physio_notes.db")
DOCS_DIR = os.path.join(os.path.dirname(DB_PATH), "documentos")
os.makedirs(DOCS_DIR, exist_ok=True)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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
        cols = [r[1] for r in conn.execute("PRAGMA table_info(paciente)").fetchall()]
        if "anamnese" not in cols:
            conn.execute("ALTER TABLE paciente ADD COLUMN anamnese TEXT")
        if "cpf" not in cols:
            conn.execute("ALTER TABLE paciente ADD COLUMN cpf TEXT")
        if "endereco" not in cols:
            conn.execute("ALTER TABLE paciente ADD COLUMN endereco TEXT")
        # Multi-tenant: dono de cada paciente
        cols_pac = [r[1] for r in conn.execute("PRAGMA table_info(paciente)").fetchall()]
        if "owner_email" not in cols_pac:
            conn.execute("ALTER TABLE paciente ADD COLUMN owner_email TEXT")
        if "conduta_tratamento" not in cols_pac:
            conn.execute("ALTER TABLE paciente ADD COLUMN conduta_tratamento TEXT")

        # Unicidade CPF por fisio: mesmo CPF não pode ser cadastrado duas vezes pela mesma owner_email
        # Pacientes deletados ficam fora da restrição (partial index)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_paciente_cpf_owner
            ON paciente(cpf, owner_email)
            WHERE cpf IS NOT NULL AND deletado_em IS NULL
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

        # Soft delete
        for tabela in ("paciente", "sessao"):
            cols_t = [r[1] for r in conn.execute(f"PRAGMA table_info({tabela})").fetchall()]
            if "deletado_em" not in cols_t:
                conn.execute(f"ALTER TABLE {tabela} ADD COLUMN deletado_em TEXT")

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

        # Multi-tenant: owner em nota_fiscal
        cols_nf = [r[1] for r in conn.execute("PRAGMA table_info(nota_fiscal)").fetchall()]
        if "owner_email" not in cols_nf:
            conn.execute("ALTER TABLE nota_fiscal ADD COLUMN owner_email TEXT")

        # Pacotes de sessões
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pacote (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id     INTEGER NOT NULL REFERENCES paciente(id),
                total_sessoes   INTEGER NOT NULL,
                sessoes_usadas  INTEGER NOT NULL DEFAULT 0,
                valor_pago      REAL,
                data_pagamento  TEXT,
                descricao       TEXT,
                criado_em       TEXT    NOT NULL,
                deletado_em     TEXT
            )
        """)

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

        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_sessao_paciente_id ON sessao(paciente_id);
            CREATE INDEX IF NOT EXISTS idx_sessao_criado_em ON sessao(criado_em);
            CREATE INDEX IF NOT EXISTS idx_audio_chunk_sessao_id ON audio_chunk(sessao_id);
            CREATE INDEX IF NOT EXISTS idx_pacote_paciente_id ON pacote(paciente_id);
            CREATE INDEX IF NOT EXISTS idx_paciente_owner_email ON paciente(owner_email);
            CREATE INDEX IF NOT EXISTS idx_api_uso_criado_em ON api_uso(criado_em);
            CREATE INDEX IF NOT EXISTS idx_api_uso_owner_email ON api_uso(owner_email);
            CREATE INDEX IF NOT EXISTS idx_documento_paciente_id ON documento(paciente_id);
        """)
        conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict:
    return dict(row) if row else None


# ---------- Paciente ----------

def criar_paciente(nome: str, data_nascimento: str | None, observacoes: str | None, anamnese: str | None = None, cpf: str | None = None, endereco: str | None = None, owner_email: str | None = None, conduta_tratamento: str | None = None) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO paciente (nome, data_nascimento, observacoes, anamnese, cpf, endereco, owner_email, conduta_tratamento, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (nome, data_nascimento, observacoes, anamnese, cpf, endereco, owner_email, conduta_tratamento, _now()),
        )
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM paciente WHERE id = ?", (cur.lastrowid,)).fetchone())


def atualizar_paciente(paciente_id: int, nome: str, data_nascimento: str | None, anamnese: str | None, cpf: str | None = None, endereco: str | None = None, conduta_tratamento: str | None = None) -> dict:
    with get_conn() as conn:
        conn.execute(
            "UPDATE paciente SET nome = ?, data_nascimento = ?, anamnese = ?, cpf = ?, endereco = ?, conduta_tratamento = ? WHERE id = ?",
            (nome, data_nascimento, anamnese, cpf, endereco, conduta_tratamento, paciente_id),
        )
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM paciente WHERE id = ?", (paciente_id,)).fetchone())


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
        return [_row_to_dict(r) for r in rows]


def deletar_paciente(paciente_id: int):
    """Soft delete: marca paciente e todas as suas sessões como deletados."""
    now = _now()
    with get_conn() as conn:
        conn.execute("UPDATE paciente SET deletado_em = ? WHERE id = ?", (now, paciente_id))
        conn.execute("UPDATE sessao SET deletado_em = ? WHERE paciente_id = ? AND deletado_em IS NULL", (now, paciente_id))
        conn.commit()


def get_paciente(paciente_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM paciente WHERE id = ?", (paciente_id,)).fetchone()
        return _row_to_dict(row)


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


def encerrar_sessao(sessao_id: int, owner_email: str | None = None) -> dict:
    """Encerra sessão. Se não há pacote ativo e owner tem valor_sessao_avulsa configurado,
    cria procedimento_extra automático. Retorna {teve_pacote, sessao_avulsa_valor}."""
    from datetime import date
    with get_conn() as conn:
        sessao = conn.execute("SELECT paciente_id, data FROM sessao WHERE id = ?", (sessao_id,)).fetchone()
        cur = conn.execute(
            "UPDATE sessao SET status = 'encerrada' WHERE id = ? AND status = 'aberta'",
            (sessao_id,)
        )
        if cur.rowcount == 0:
            return {"teve_pacote": True, "sessao_avulsa_valor": None, "_ja_encerrada": True}
        sessao_avulsa_valor = None
        if sessao:
            teve_pacote = _usar_sessao_pacote(conn, sessao["paciente_id"])
            if not teve_pacote and owner_email:
                row_cfg = conn.execute(
                    "SELECT valor_sessao_avulsa FROM usuario_google WHERE email = ?", (owner_email,)
                ).fetchone()
                valor = row_cfg["valor_sessao_avulsa"] if row_cfg else None
                if valor and valor > 0:
                    data_sessao = sessao["data"] or date.today().isoformat()
                    conn.execute(
                        "INSERT INTO procedimento_extra (sessao_id, paciente_id, descricao, valor, data, criado_em) VALUES (?, ?, ?, ?, ?, ?)",
                        (sessao_id, sessao["paciente_id"], "Sessão avulsa", valor, data_sessao, _now()),
                    )
                    sessao_avulsa_valor = valor
        conn.commit()
        return {"teve_pacote": teve_pacote if sessao else True, "sessao_avulsa_valor": sessao_avulsa_valor}


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
                "INSERT INTO procedimento_extra (sessao_id, paciente_id, descricao, valor, data, criado_em) VALUES (?, ?, ?, ?, ?, ?)",
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

def registrar_uso(tipo: str, modelo: str, input_tokens: int, output_tokens: int, custo_usd: float, owner_email: str | None = None):
    with get_conn() as conn:
        # Adiciona owner_email na api_uso se ainda não existir
        cols = [r[1] for r in conn.execute("PRAGMA table_info(api_uso)").fetchall()]
        if "owner_email" not in cols:
            conn.execute("ALTER TABLE api_uso ADD COLUMN owner_email TEXT")
        conn.execute(
            "INSERT INTO api_uso (tipo, modelo, input_tokens, output_tokens, custo_usd, owner_email, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tipo, modelo, input_tokens, output_tokens, custo_usd, owner_email, _now()),
        )
        conn.commit()


def get_billing_mes(ano_mes: str, owner_email: str | None = None) -> dict:
    """ano_mes no formato YYYY-MM. Retorna totais e breakdown por tipo."""
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
                      SUM(custo_usd) as custo_usd
               FROM api_uso WHERE strftime('%Y-%m', criado_em) = ? {owner_filter}
               GROUP BY tipo ORDER BY custo_usd DESC""",
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

def criar_pacote(paciente_id: int, total_sessoes: int, valor_pago: float | None, data_pagamento: str | None, descricao: str | None) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pacote (paciente_id, total_sessoes, valor_pago, data_pagamento, descricao, criado_em) VALUES (?, ?, ?, ?, ?, ?)",
            (paciente_id, total_sessoes, valor_pago, data_pagamento, descricao, _now()),
        )
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM pacote WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_pacotes_paciente(paciente_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pacote WHERE paciente_id = ? AND deletado_em IS NULL ORDER BY criado_em DESC",
            (paciente_id,),
        ).fetchall()
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
            "INSERT INTO procedimento_extra (sessao_id, paciente_id, descricao, valor, data, criado_em) VALUES (?, ?, ?, ?, ?, ?)",
            (sessao_id, paciente_id, descricao, valor, data, _now()),
        )
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM procedimento_extra WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_procedimento(proc_id: int) -> dict | None:
    with get_conn() as conn:
        return _row_to_dict(conn.execute(
            "SELECT * FROM procedimento_extra WHERE id = ? AND deletado_em IS NULL", (proc_id,)
        ).fetchone())


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
            pk_where.append("p.owner_email = ?")
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
            pe_where.append("p.owner_email = ?")
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

        # meses disponíveis (união de datas de pacotes e procedimentos)
        meses_rows = conn.execute("""
            SELECT DISTINCT mes FROM (
                SELECT strftime('%Y-%m', COALESCE(data_pagamento, DATE(criado_em))) AS mes
                FROM pacote WHERE deletado_em IS NULL AND valor_pago IS NOT NULL
                UNION
                SELECT strftime('%Y-%m', COALESCE(data, criado_em)) AS mes
                FROM procedimento_extra WHERE deletado_em IS NULL AND valor IS NOT NULL
            ) ORDER BY mes DESC
        """).fetchall()

        # pacientes disponíveis (união de pacotes e procedimentos)
        pacientes_rows = conn.execute("""
            SELECT DISTINCT p.id, p.nome FROM paciente p
            WHERE p.deletado_em IS NULL AND (
                EXISTS (SELECT 1 FROM pacote pk WHERE pk.paciente_id = p.id AND pk.deletado_em IS NULL AND pk.valor_pago IS NOT NULL)
                OR
                EXISTS (SELECT 1 FROM procedimento_extra pe WHERE pe.paciente_id = p.id AND pe.deletado_em IS NULL AND pe.valor IS NOT NULL)
            )
            ORDER BY p.nome COLLATE NOCASE
        """).fetchall()

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
    _init_usuario_table()
    with get_conn() as conn:
        conn.execute("UPDATE usuario_google SET ativo = 0 WHERE email = ?", (email,))
        conn.commit()


# ---------- Configurações do usuário ----------

VALOR_SESSAO_AVULSA_PADRAO = 280.0


def _migrar_config_usuario():
    """Garante que usuario_google tem a coluna valor_sessao_avulsa."""
    _init_usuario_table()
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(usuario_google)").fetchall()]
        if "valor_sessao_avulsa" not in cols:
            conn.execute("ALTER TABLE usuario_google ADD COLUMN valor_sessao_avulsa REAL")
        # Preenche padrão para usuários que ainda não configuraram
        conn.execute(
            "UPDATE usuario_google SET valor_sessao_avulsa = ? WHERE valor_sessao_avulsa IS NULL",
            (VALOR_SESSAO_AVULSA_PADRAO,),
        )
        conn.commit()


def get_config_usuario(owner_email: str) -> dict:
    _migrar_config_usuario()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT valor_sessao_avulsa FROM usuario_google WHERE email = ?", (owner_email,)
        ).fetchone()
        valor = (row["valor_sessao_avulsa"] if row else None) or VALOR_SESSAO_AVULSA_PADRAO
        return {"valor_sessao_avulsa": valor}


def set_config_usuario(owner_email: str, valor_sessao_avulsa: float | None):
    _migrar_config_usuario()
    with get_conn() as conn:
        conn.execute(
            "UPDATE usuario_google SET valor_sessao_avulsa = ? WHERE email = ?",
            (valor_sessao_avulsa, owner_email),
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

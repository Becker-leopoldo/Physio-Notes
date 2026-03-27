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

        conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict:
    return dict(row) if row else None


# ---------- Paciente ----------

def criar_paciente(nome: str, data_nascimento: str | None, observacoes: str | None, anamnese: str | None = None) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO paciente (nome, data_nascimento, observacoes, anamnese, criado_em) VALUES (?, ?, ?, ?, ?)",
            (nome, data_nascimento, observacoes, anamnese, _now()),
        )
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM paciente WHERE id = ?", (cur.lastrowid,)).fetchone())


def atualizar_paciente(paciente_id: int, nome: str, data_nascimento: str | None, anamnese: str | None) -> dict:
    with get_conn() as conn:
        conn.execute(
            "UPDATE paciente SET nome = ?, data_nascimento = ?, anamnese = ? WHERE id = ?",
            (nome, data_nascimento, anamnese, paciente_id),
        )
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM paciente WHERE id = ?", (paciente_id,)).fetchone())


def listar_pacientes() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
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
            WHERE p.deletado_em IS NULL ORDER BY p.nome COLLATE NOCASE
        """).fetchall()
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


def sessao_aberta_do_paciente(paciente_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sessao WHERE paciente_id = ? AND status = 'aberta' AND deletado_em IS NULL ORDER BY criado_em DESC LIMIT 1",
            (paciente_id,),
        ).fetchone()
        return _row_to_dict(row)


def _usar_sessao_pacote(conn, paciente_id: int):
    """Abate 1 sessão do pacote ativo do paciente, se houver."""
    row = conn.execute(
        """SELECT id FROM pacote WHERE paciente_id = ? AND deletado_em IS NULL
           AND sessoes_usadas < total_sessoes ORDER BY criado_em DESC LIMIT 1""",
        (paciente_id,)
    ).fetchone()
    if row:
        conn.execute("UPDATE pacote SET sessoes_usadas = sessoes_usadas + 1 WHERE id = ?", (row["id"],))


def encerrar_sessao(sessao_id: int):
    with get_conn() as conn:
        sessao = conn.execute("SELECT paciente_id FROM sessao WHERE id = ?", (sessao_id,)).fetchone()
        conn.execute("UPDATE sessao SET status = 'encerrada' WHERE id = ?", (sessao_id,))
        if sessao:
            _usar_sessao_pacote(conn, sessao["paciente_id"])
        conn.commit()


def cancelar_sessao(sessao_id: int):
    """Remove sessão aberta sem áudio (criada por engano)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM sessao WHERE id = ? AND status = 'aberta'", (sessao_id,))
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

def registrar_uso(tipo: str, modelo: str, input_tokens: int, output_tokens: int, custo_usd: float):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO api_uso (tipo, modelo, input_tokens, output_tokens, custo_usd, criado_em) VALUES (?, ?, ?, ?, ?, ?)",
            (tipo, modelo, input_tokens, output_tokens, custo_usd, _now()),
        )
        conn.commit()


def get_billing_mes(ano_mes: str) -> dict:
    """ano_mes no formato YYYY-MM. Retorna totais e breakdown por tipo."""
    with get_conn() as conn:
        totais = conn.execute(
            """SELECT COUNT(*) as total_chamadas,
                      SUM(input_tokens) as total_input,
                      SUM(output_tokens) as total_output,
                      SUM(custo_usd) as total_usd
               FROM api_uso WHERE strftime('%Y-%m', criado_em) = ?""",
            (ano_mes,),
        ).fetchone()

        por_tipo = conn.execute(
            """SELECT tipo,
                      COUNT(*) as chamadas,
                      SUM(input_tokens) as input_tokens,
                      SUM(output_tokens) as output_tokens,
                      SUM(custo_usd) as custo_usd
               FROM api_uso WHERE strftime('%Y-%m', criado_em) = ?
               GROUP BY tipo ORDER BY custo_usd DESC""",
            (ano_mes,),
        ).fetchall()

        return {
            "mes": ano_mes,
            "total_chamadas": totais["total_chamadas"] or 0,
            "total_input_tokens": totais["total_input"] or 0,
            "total_output_tokens": totais["total_output"] or 0,
            "total_usd": round(totais["total_usd"] or 0, 6),
            "por_tipo": [_row_to_dict(r) for r in por_tipo],
        }


def get_billing_meses() -> list[dict]:
    """Retorna lista de meses com totais, do mais recente ao mais antigo."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT strftime('%Y-%m', criado_em) as mes,
                      COUNT(*) as total_chamadas,
                      SUM(input_tokens) as total_input_tokens,
                      SUM(output_tokens) as total_output_tokens,
                      ROUND(SUM(custo_usd), 6) as total_usd
               FROM api_uso
               GROUP BY mes ORDER BY mes DESC""",
        ).fetchall()
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

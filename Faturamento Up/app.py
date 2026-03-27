"""
Sistema de Faturamento UP IT — Backend FastAPI
Executa: uvicorn app:app --reload
"""
import os
import re
import io
import json
import unicodedata
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import init_db, get_db, NotaFiscal, TransacaoOFX, AliasDB

# ── Configuracao ──────────────────────────────────────────────
_local_assets  = Path(__file__).parent / "assets"
_parent_assets = Path(__file__).parent.parent / "assets"
ASSETS_DIR = _local_assets if _local_assets.exists() else _parent_assets
FRONTEND_DIR = Path(__file__).parent / "frontend"
CREDS_FILE   = Path(__file__).parent / "credenciais.json"
SHEET_URL    = "https://docs.google.com/spreadsheets/d/1M03peFMUukZuS0k7I44to3OUap-BrhaiHL-8f8PAZUo/edit?usp=sharing"

MESES_SHEETS = {
    1:  "Receitas Janeiro",  2:  "Receitas Fevereiro", 3:  "Receitas Março",
    4:  "Receitas Abril",    5:  "Receitas Maio",       6:  "Receitas Junho",
    7:  "Receitas Julho",    8:  "Receitas Agosto",     9:  "Receitas Setembro",
    10: "Receitas Outubro",  11: "Receitas Novembro",   12: "Receitas Dezembro",
}

# Colunas da planilha (1-based)
_S_CLIENTE   = 2
_S_STATUS    = 4   # D
_S_EXTRATO   = 5   # E
_S_DATA_PGTO = 6   # F
_S_VALOR_CR  = 7   # G
_S_TIPO      = 8   # H
_S_FORMA     = 9   # I
_S_NOTA      = 11  # K
_S_VENC      = 13  # M
_S_VALOR_NF  = 17  # Q

IUGU_MEMO_KEY = "up solu"
IUGU_TAXA_MAX = 10.00

MESES_NOMES = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

MESES_JANELAS = {
    1:  (datetime(2026,  1, 10), datetime(2026,  2, 28)),
    2:  (datetime(2026,  2, 10), datetime(2026,  3, 31)),
    3:  (datetime(2026,  3, 10), datetime(2026,  4, 30)),
    4:  (datetime(2026,  4, 10), datetime(2026,  5, 31)),
    5:  (datetime(2026,  5, 10), datetime(2026,  6, 30)),
    6:  (datetime(2026,  6, 10), datetime(2026,  7, 31)),
    7:  (datetime(2026,  7, 10), datetime(2026,  8, 31)),
    8:  (datetime(2026,  8, 10), datetime(2026,  9, 30)),
    9:  (datetime(2026,  9, 10), datetime(2026, 10, 31)),
    10: (datetime(2026, 10, 10), datetime(2026, 11, 30)),
    11: (datetime(2026, 11, 10), datetime(2026, 12, 31)),
    12: (datetime(2026, 12, 10), datetime(2027,  1, 31)),
}

PAYER_GROUPS = {
    "REDE IMPAR":       "impar",
    "DASA":             "impar",
    "SAINT GOBAIN":     "saint_gobain",
    "SAINT GOBAIN PPC": "saint_gobain",
}

BASE_ALIASES = {
    "impar serv":         "REDE IMPAR",
    "impar servicos":     "REDE IMPAR",
    "impar":              "REDE IMPAR",
    "ez tec":             "EZTEC",
    "eztec":              "EZTEC",
    "saint gobain":       "SAINT GOBAIN",
    "saint-gobain":       "SAINT GOBAIN",
    "simpress":           "SIMPRESS",
    "dynatest":           "DYNATEST",
    "louis d c b":        "LOUIS DREYFUS COMPANY BRASIL S.A.",
    "louis dreyfus":      "LOUIS DREYFUS COMPANY BRASIL S.A.",
    "sociedade regional": "SAO LEOPOLDO MANDIC",
    "shinagawa":          "SHINAGAWA",
}

MEMO_IGNORE = [
    "saldo total disp", "rendimentos rend", "estorno tar",
    "saldo anterior", "rend pago aplic",
]

# ── FastAPI ───────────────────────────────────────────────────
app = FastAPI(title="Faturamento UP IT", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

init_db()

# Serve CSS global do design system
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

# Serve frontend HTML
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ── Helpers de normalização ───────────────────────────────────
def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", " ", t.lower()).strip()

def close(a: float, b: float) -> bool:
    return abs(a - b) <= 0.50

def extrair_cnpj(texto: str) -> Optional[str]:
    m = re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", texto)
    return m.group(0) if m else None

def nome_extrato(memo: str) -> str:
    skip = {"RECEBIMENTOS", "PIX", "RECEBIDO", "TED", "RECEBIDA", "ENTRADA", "SISPAG"}
    parts = [p for p in memo.split() if p.upper() not in skip and len(p) > 2]
    return " ".join(parts[:8])


# ── Aliases mesclados (DB + hardcoded) ───────────────────────
def build_aliases(db: Session) -> dict:
    aliases = dict(BASE_ALIASES)
    for row in db.query(AliasDB).all():
        aliases[row.chave] = row.nome_cliente
    return aliases


# ── Parsing OFX ───────────────────────────────────────────────
def parse_ofx(content: str, filename: str, db: Session) -> dict:
    """Lê conteúdo OFX e insere transações novas no banco. Retorna contagens."""
    novos = 0
    duplicados = 0
    ignorados = 0

    for b in re.findall(r"<STMTTRN>(.*?)</STMTTRN>", content, re.DOTALL):
        def fld(tag):
            m = re.search(rf"<{tag}>([^\n<]+)", b)
            return m.group(1).strip() if m else ""

        if fld("TRNTYPE") != "CREDIT":
            continue
        amount = float(fld("TRNAMT") or 0)
        if amount <= 0:
            continue
        try:
            date = datetime.strptime(fld("DTPOSTED")[:8], "%Y%m%d")
        except ValueError:
            continue
        memo = fld("MEMO")
        if any(p in norm(memo) for p in MEMO_IGNORE):
            ignorados += 1
            continue
        fitid = fld("FITID") or None

        if fitid and db.query(TransacaoOFX).filter(TransacaoOFX.fitid == fitid).first():
            duplicados += 1
            continue

        tx = TransacaoOFX(
            fitid=fitid, date=date, amount=amount,
            memo=memo, ofx_filename=filename, used=False,
        )
        db.add(tx)
        novos += 1

    db.commit()
    return {"novos": novos, "duplicados": duplicados, "ignorados": ignorados}


# ── Engine de matching ────────────────────────────────────────
def _get_credits_for_month(mes: int, db: Session) -> list[dict]:
    start, end = MESES_JANELAS[mes]
    rows = db.query(TransacaoOFX).filter(
        TransacaoOFX.date >= start,
        TransacaoOFX.date <= end,
        TransacaoOFX.used == False,
    ).order_by(TransacaoOFX.date).all()
    return [
        {
            "id": r.id, "date": r.date, "amount": r.amount,
            "memo": r.memo, "memo_norm": norm(r.memo),
            "cnpj": extrair_cnpj(r.memo), "used": False,
        }
        for r in rows
    ]

def _run_match_invoices(invoices: list[dict], credits: list[dict], aliases: dict):
    def client_match(cr, cliente):
        cn = norm(cliente)
        if any(a in cr["memo_norm"] and norm(m) == cn for a, m in aliases.items()):
            return True
        if cr["cnpj"] and aliases.get(cr["cnpj"]) == cliente:
            return True
        return cn in cr["memo_norm"]

    def mark(inv, cr, tipo):
        inv["pago_em"] = cr["date"]
        inv["fonte"]   = cr["memo"]
        inv["tipo"]    = tipo
        inv["cr_amount"] = cr["amount"]
        inv["matched_cr_id"] = cr["id"]
        cr["used"] = True

    # Passo 1: valor exato + cliente identificado
    for inv in invoices:
        if inv.get("fonte") or inv.get("is_boleto"): continue
        for cr in credits:
            if cr["used"] or not close(cr["amount"], inv["valor"]): continue
            if client_match(cr, inv["cliente"]):
                mark(inv, cr, "Valor exato")
                break

    # Passo 2: valor exato sem identificar cliente
    for inv in invoices:
        if inv.get("fonte") or inv.get("is_boleto"): continue
        for cr in credits:
            if cr["used"] or not close(cr["amount"], inv["valor"]): continue
            mark(inv, cr, "Valor exato (sem id. cliente)")
            break

    # Passo 3: soma de NFs — mesmo grupo de pagador
    groups: dict[str, list] = {}
    for inv in invoices:
        if inv.get("fonte") or inv.get("is_boleto"): continue
        g = PAYER_GROUPS.get(inv["cliente"], inv["cliente"])
        groups.setdefault(g, []).append(inv)

    for _, group_invs in groups.items():
        if len(group_invs) < 2: continue
        aliases_grupo = {a for a, m in aliases.items()
                         if any(norm(m) == norm(inv["cliente"]) for inv in group_invs)}
        cnpjs_grupo   = {cnpj for cnpj, cli in aliases.items()
                         if re.match(r"\d{2}\.\d{3}\.\d{3}", cnpj)
                         and any(norm(cli) == norm(inv["cliente"]) for inv in group_invs)}
        candidates = [cr for cr in credits if not cr["used"] and (
            any(a in cr["memo_norm"] for a in aliases_grupo) or
            (cr["cnpj"] and cr["cnpj"] in cnpjs_grupo)
        )]
        for cr in candidates:
            for r in range(2, len(group_invs) + 1):
                for combo in combinations(group_invs, r):
                    if close(cr["amount"], sum(i["valor"] for i in combo)):
                        for inv in combo:
                            mark(inv, cr, f"Soma {r} NFs")
                        break
                else:
                    continue
                break

def _match_boletos_iugu(boletos: list[dict], credits: list[dict]) -> tuple:
    if not boletos:
        return None, None
    bruto = round(sum(b["valor"] for b in boletos), 2)
    iugu_crs = [cr for cr in credits if not cr["used"] and IUGU_MEMO_KEY in cr["memo_norm"]]
    if not iugu_crs:
        return None, None

    validos = []
    for r in range(1, min(len(iugu_crs) + 1, 5)):
        for combo in combinations(iugu_crs, r):
            soma = round(sum(c["amount"] for c in combo), 2)
            taxa = round(bruto - soma, 2)
            taxa_unit = taxa / len(boletos)
            if taxa > 0 and taxa_unit <= IUGU_TAXA_MAX:
                validos.append((list(combo), taxa))

    if validos:
        validos.sort(key=lambda x: max(c["date"] for c in x[0]))
        combo, taxa = validos[0]
        data_ref = max(c["date"] for c in combo)
        soma = round(sum(c["amount"] for c in combo), 2)
        for b in boletos:
            b["pago_em"]       = data_ref
            b["fonte"]         = combo[0]["memo"]
            b["tipo"]          = "iugu Boleto"
            b["cr_amount"]     = b["valor"]
            b["matched_cr_id"] = combo[0]["id"]
        for c in combo:
            c["used"] = True
        return combo, taxa

    return None, None


# ── Schemas Pydantic ──────────────────────────────────────────
class NotaCreate(BaseModel):
    mes_ref: int
    cliente: str
    numero_nf: Optional[str] = None
    data_vencimento: Optional[str] = None   # "DD/MM/YYYY"
    valor: float
    forma_pagamento: Optional[str] = "PIX"

class NotaUpdate(BaseModel):
    cliente: Optional[str] = None
    numero_nf: Optional[str] = None
    data_vencimento: Optional[str] = None
    valor: Optional[float] = None
    forma_pagamento: Optional[str] = None
    status: Optional[str] = None

class AliasCreate(BaseModel):
    chave: str
    nome_cliente: str


# ── Rotas frontend ────────────────────────────────────────────
@app.get("/")
def index():
    return FileResponse(str(FRONTEND_DIR / "dashboard.html"))

@app.get("/notas")
def page_notas():
    return FileResponse(str(FRONTEND_DIR / "notas.html"))

@app.get("/extrato")
def page_extrato():
    return FileResponse(str(FRONTEND_DIR / "extrato.html"))

@app.get("/conciliacao")
def page_conciliacao():
    return FileResponse(str(FRONTEND_DIR / "conciliacao.html"))

@app.get("/aliases")
def page_aliases():
    return FileResponse(str(FRONTEND_DIR / "aliases.html"))


# ── API: Dashboard ────────────────────────────────────────────
@app.get("/api/summary")
def api_summary(db: Session = Depends(get_db)):
    nfs = db.query(NotaFiscal).filter(NotaFiscal.deleted == False).all()

    por_mes = {}
    for nf in nfs:
        m = nf.mes_ref
        if m not in por_mes:
            por_mes[m] = {"mes": m, "nome": MESES_NOMES.get(m, str(m)),
                          "pago": 0.0, "aberto": 0.0, "sem_match": 0.0, "total": 0.0}
        por_mes[m]["total"] += nf.valor
        if nf.status == "PAGO":
            por_mes[m]["pago"] += nf.valor
        elif nf.status == "SEM_MATCH":
            por_mes[m]["sem_match"] += nf.valor
        else:
            por_mes[m]["aberto"] += nf.valor

    total_faturado = sum(n.valor for n in nfs)
    total_pago     = sum(n.valor for n in nfs if n.status == "PAGO")
    total_aberto   = sum(n.valor for n in nfs if n.status == "ABERTO")
    total_sem      = sum(n.valor for n in nfs if n.status == "SEM_MATCH")

    return {
        "total_nfs":       len(nfs),
        "total_faturado":  round(total_faturado, 2),
        "total_pago":      round(total_pago, 2),
        "total_aberto":    round(total_aberto, 2),
        "total_sem_match": round(total_sem, 2),
        "por_mes":         sorted(por_mes.values(), key=lambda x: x["mes"]),
    }


# ── API: Notas Fiscais ────────────────────────────────────────
@app.get("/api/notas")
def api_list_notas(
    mes: Optional[int] = None,
    status: Optional[str] = None,
    cliente: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(NotaFiscal).filter(NotaFiscal.deleted == False)
    if mes:
        q = q.filter(NotaFiscal.mes_ref == mes)
    if status:
        q = q.filter(NotaFiscal.status == status.upper())
    if cliente:
        q = q.filter(NotaFiscal.cliente.ilike(f"%{cliente}%"))
    nfs = q.order_by(NotaFiscal.mes_ref, NotaFiscal.cliente).all()
    return [_nf_to_dict(n) for n in nfs]


@app.post("/api/notas", status_code=201)
def api_create_nota(data: NotaCreate, db: Session = Depends(get_db)):
    dv = None
    if data.data_vencimento:
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                dv = datetime.strptime(data.data_vencimento[:10], fmt)
                break
            except ValueError:
                continue
    nf = NotaFiscal(
        mes_ref=data.mes_ref, cliente=data.cliente.upper().strip(),
        numero_nf=data.numero_nf, data_vencimento=dv,
        valor=round(data.valor, 2), forma_pagamento=data.forma_pagamento,
        status="ABERTO",
    )
    db.add(nf)
    db.commit()
    db.refresh(nf)
    return _nf_to_dict(nf)


@app.put("/api/notas/{nf_id}")
def api_update_nota(nf_id: int, data: NotaUpdate, db: Session = Depends(get_db)):
    nf = db.query(NotaFiscal).filter(NotaFiscal.id == nf_id).first()
    if not nf or nf.deleted:
        raise HTTPException(404, "Nota não encontrada")
    if data.cliente is not None:
        nf.cliente = data.cliente.upper().strip()
    if data.numero_nf is not None:
        nf.numero_nf = data.numero_nf
    if data.valor is not None:
        nf.valor = round(data.valor, 2)
    if data.forma_pagamento is not None:
        nf.forma_pagamento = data.forma_pagamento
    if data.status is not None:
        nf.status = data.status.upper()
    if data.data_vencimento is not None:
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                nf.data_vencimento = datetime.strptime(data.data_vencimento[:10], fmt)
                break
            except ValueError:
                continue
    db.commit()
    db.refresh(nf)
    return _nf_to_dict(nf)


@app.delete("/api/notas/{nf_id}")
def api_delete_nota(nf_id: int, db: Session = Depends(get_db)):
    nf = db.query(NotaFiscal).filter(NotaFiscal.id == nf_id).first()
    if not nf or nf.deleted:
        raise HTTPException(404, "Nota não encontrada")
    nf.deleted = True
    db.commit()
    return {"ok": True}


@app.post("/api/notas/importar-csv")
async def api_import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Importa NFs de um CSV com colunas:
    mes_ref, cliente, numero_nf, data_vencimento, valor, forma_pagamento
    """
    import csv
    content = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    criadas = 0
    erros = []
    for i, row in enumerate(reader, 2):
        try:
            mes = int(row.get("mes_ref", 0))
            valor = float(re.sub(r"[R$,\s]", "", str(row.get("valor", "0"))))
            if mes < 1 or mes > 12 or valor <= 0:
                erros.append(f"Linha {i}: mes ou valor inválido")
                continue
            dv = None
            venc = str(row.get("data_vencimento", "")).strip()
            if venc:
                for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
                    try:
                        dv = datetime.strptime(venc[:10], fmt)
                        break
                    except ValueError:
                        continue
            nf = NotaFiscal(
                mes_ref=mes,
                cliente=str(row.get("cliente", "")).upper().strip(),
                numero_nf=str(row.get("numero_nf", "")).strip() or None,
                data_vencimento=dv,
                valor=round(valor, 2),
                forma_pagamento=str(row.get("forma_pagamento", "PIX")).upper().strip(),
                status="ABERTO",
            )
            db.add(nf)
            criadas += 1
        except Exception as e:
            erros.append(f"Linha {i}: {e}")
    db.commit()
    return {"criadas": criadas, "erros": erros}


# ── API: Extratos OFX ─────────────────────────────────────────
@app.post("/api/extratos/upload")
async def api_upload_ofx(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith(".ofx"):
        raise HTTPException(400, "Apenas arquivos .ofx são aceitos")
    raw = await file.read()
    content = raw.decode("cp1252", errors="replace")
    result = parse_ofx(content, file.filename, db)
    return result


@app.get("/api/extratos")
def api_list_extratos(
    mes: Optional[int] = None,
    used: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    q = db.query(TransacaoOFX)
    if mes:
        start, end = MESES_JANELAS[mes]
        q = q.filter(TransacaoOFX.date >= start, TransacaoOFX.date <= end)
    if used is not None:
        q = q.filter(TransacaoOFX.used == used)
    txs = q.order_by(TransacaoOFX.date.desc()).all()
    return [
        {
            "id": t.id, "fitid": t.fitid, "date": t.date.strftime("%d/%m/%Y") if t.date else None,
            "amount": t.amount, "memo": t.memo, "ofx_filename": t.ofx_filename,
            "used": t.used, "mes_usado": t.mes_usado,
        }
        for t in txs
    ]


@app.delete("/api/extratos/{tx_id}")
def api_delete_extrato(tx_id: int, db: Session = Depends(get_db)):
    tx = db.query(TransacaoOFX).filter(TransacaoOFX.id == tx_id).first()
    if not tx:
        raise HTTPException(404, "Transação não encontrada")
    db.delete(tx)
    db.commit()
    return {"ok": True}


# ── API: Conciliação ──────────────────────────────────────────
@app.post("/api/conciliar/{mes}")
def api_conciliar(mes: int, db: Session = Depends(get_db)):
    if mes not in MESES_JANELAS:
        raise HTTPException(400, "Mês inválido (1-12)")

    aliases = build_aliases(db)

    # Reseta status das NFs do mês (exceto overrides manuais)
    nfs_db = db.query(NotaFiscal).filter(
        NotaFiscal.mes_ref == mes,
        NotaFiscal.deleted == False,
    ).all()

    # Resetar créditos do mês como não utilizados
    start, end = MESES_JANELAS[mes]
    txs_db = db.query(TransacaoOFX).filter(
        TransacaoOFX.date >= start, TransacaoOFX.date <= end
    ).all()
    for tx in txs_db:
        if tx.mes_usado == MESES_NOMES.get(mes):
            tx.used = False
            tx.mes_usado = None

    # Monta listas de trabalho
    all_inv = [
        {
            "id": nf.id, "cliente": nf.cliente, "nota": nf.numero_nf,
            "valor": nf.valor, "is_boleto": (nf.forma_pagamento == "BOLETO"),
            "pago_em": None, "fonte": None, "tipo": None,
            "cr_amount": None, "matched_cr_id": None,
        }
        for nf in nfs_db
    ]
    invoices = [i for i in all_inv if not i["is_boleto"]]
    boletos  = [i for i in all_inv if i["is_boleto"]]

    credits = _get_credits_for_month(mes, db)

    _run_match_invoices(invoices, credits, aliases)
    iugu_combos, taxa_iugu = _match_boletos_iugu(boletos, credits)

    # Persiste resultados nas NFs
    matched_cr_ids = set()
    for inv in all_inv:
        nf = next(n for n in nfs_db if n.id == inv["id"])
        if inv["fonte"]:
            nf.status     = "PAGO"
            nf.pago_em    = inv["pago_em"]
            nf.fonte_memo = inv["fonte"]
            nf.tipo_match = inv["tipo"]
            nf.cr_amount  = inv["cr_amount"]
            if inv["matched_cr_id"]:
                matched_cr_ids.add(inv["matched_cr_id"])
        else:
            nf.status     = "ABERTO"
            nf.pago_em    = None
            nf.fonte_memo = None
            nf.tipo_match = None
            nf.cr_amount  = None

    # Marca créditos usados no banco
    mes_nome = MESES_NOMES.get(mes, str(mes))
    for cr in credits:
        if cr["used"]:
            tx = db.query(TransacaoOFX).filter(TransacaoOFX.id == cr["id"]).first()
            if tx:
                tx.used = True
                tx.mes_usado = mes_nome

    # Aprende CNPJs novos como aliases
    for inv in all_inv:
        if inv.get("fonte"):
            cnpj = extrair_cnpj(inv["fonte"])
            if cnpj:
                existing = db.query(AliasDB).filter(AliasDB.chave == cnpj).first()
                if not existing:
                    db.add(AliasDB(chave=cnpj, nome_cliente=inv["cliente"]))

    db.commit()

    # Monta resultado
    matched_nf  = [i for i in invoices if i["fonte"]]
    aberto_nf   = [i for i in invoices if not i["fonte"]]
    matched_bol = [b for b in boletos if b["fonte"]]
    aberto_bol  = [b for b in boletos if not b["fonte"]]

    return {
        "mes": mes,
        "nome": mes_nome,
        "nfs":      {"total": len(invoices), "pago": len(matched_nf), "aberto": len(aberto_nf)},
        "boletos":  {"total": len(boletos),  "pago": len(matched_bol), "aberto": len(aberto_bol)},
        "taxa_iugu": round(taxa_iugu, 2) if taxa_iugu else None,
        "taxa_por_boleto": round(taxa_iugu / len(boletos), 2) if taxa_iugu and boletos else None,
        "detalhes": [_inv_result(i) for i in all_inv],
    }


# ── API: Aliases ──────────────────────────────────────────────
@app.get("/api/aliases")
def api_list_aliases(db: Session = Depends(get_db)):
    rows = db.query(AliasDB).order_by(AliasDB.chave).all()
    return [{"id": r.id, "chave": r.chave, "nome_cliente": r.nome_cliente} for r in rows]


@app.post("/api/aliases", status_code=201)
def api_create_alias(data: AliasCreate, db: Session = Depends(get_db)):
    existing = db.query(AliasDB).filter(AliasDB.chave == data.chave).first()
    if existing:
        existing.nome_cliente = data.nome_cliente
        db.commit()
        db.refresh(existing)
        return {"id": existing.id, "chave": existing.chave, "nome_cliente": existing.nome_cliente}
    row = AliasDB(chave=data.chave, nome_cliente=data.nome_cliente.upper().strip())
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "chave": row.chave, "nome_cliente": row.nome_cliente}


@app.delete("/api/aliases/{alias_id}")
def api_delete_alias(alias_id: int, db: Session = Depends(get_db)):
    row = db.query(AliasDB).filter(AliasDB.id == alias_id).first()
    if not row:
        raise HTTPException(404, "Alias não encontrado")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ── API: Importar do Google Sheets → DB ──────────────────────
def _parse_date(s: str) -> Optional[datetime]:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s).strip()[:10], fmt)
        except (ValueError, TypeError):
            continue
    return None

def _parse_valor(s: str) -> Optional[float]:
    try:
        return round(float(re.sub(r"[R$\s,]", "", str(s))), 2)
    except (ValueError, TypeError):
        return None

def _get_gc():
    """Retorna cliente gspread via env var GOOGLE_CREDENTIALS_JSON ou arquivo credenciais.json."""
    import gspread
    creds_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_str:
        return gspread.service_account_from_dict(json.loads(creds_str))
    if not CREDS_FILE.exists():
        raise FileNotFoundError("credenciais.json não encontrado. Configure GOOGLE_CREDENTIALS_JSON ou coloque o arquivo na pasta do app.")
    return gspread.service_account(filename=str(CREDS_FILE))

def _importar_aba(mes: int, db: Session) -> dict:
    gc = _get_gc()
    sh = gc.open_by_url(SHEET_URL)
    ws = sh.worksheet(MESES_SHEETS[mes])
    rows = ws.get_all_values()

    criadas = atualizadas = ignoradas = 0

    for row in rows[1:]:
        def c(n):
            return row[n - 1].strip() if n - 1 < len(row) else ""

        cliente = c(_S_CLIENTE)
        if not cliente:
            ignoradas += 1
            continue
        try:
            float(cliente)
            ignoradas += 1
            continue
        except (ValueError, TypeError):
            pass

        valor = _parse_valor(c(_S_VALOR_NF))
        if not valor or valor <= 0:
            ignoradas += 1
            continue

        nota     = c(_S_NOTA) or None
        forma    = c(_S_FORMA).upper() or "PIX"
        status   = "PAGO" if c(_S_STATUS).upper() == "PAGO" else "ABERTO"
        extrato  = c(_S_EXTRATO) or None
        tipo     = c(_S_TIPO) or None
        pago_em  = _parse_date(c(_S_DATA_PGTO))
        dv       = _parse_date(c(_S_VENC))
        cr_amount = _parse_valor(c(_S_VALOR_CR))

        # Busca por cliente + NF + mês (evita duplicata)
        existing = db.query(NotaFiscal).filter(
            NotaFiscal.mes_ref == mes,
            NotaFiscal.cliente == cliente.upper(),
            NotaFiscal.numero_nf == nota,
            NotaFiscal.deleted == False,
        ).first()

        if existing:
            existing.valor           = valor
            existing.forma_pagamento = forma
            existing.data_vencimento = dv
            existing.status          = status
            existing.pago_em         = pago_em
            existing.fonte_memo      = extrato
            existing.tipo_match      = tipo
            existing.cr_amount       = cr_amount
            atualizadas += 1
        else:
            db.add(NotaFiscal(
                mes_ref=mes, cliente=cliente.upper(), numero_nf=nota,
                data_vencimento=dv, valor=valor, forma_pagamento=forma,
                status=status, pago_em=pago_em,
                fonte_memo=extrato, tipo_match=tipo, cr_amount=cr_amount,
            ))
            criadas += 1

    db.commit()
    return {"mes": MESES_NOMES[mes], "criadas": criadas, "atualizadas": atualizadas, "ignoradas": ignoradas}


@app.post("/api/importar-sheets/{mes}")
def api_importar_sheets_mes(mes: int, db: Session = Depends(get_db)):
    if mes not in MESES_SHEETS:
        raise HTTPException(400, "Mês inválido (1-12)")
    try:
        return _importar_aba(mes, db)
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/importar-sheets")
def api_importar_sheets_todos(db: Session = Depends(get_db)):
    """Importa todos os meses disponíveis na planilha."""
    try:
        gc = _get_gc()
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    import gspread
    sh = gc.open_by_url(SHEET_URL)
    abas_existentes = {ws.title for ws in sh.worksheets()}

    resultados = []
    for mes, nome_aba in MESES_SHEETS.items():
        if nome_aba not in abas_existentes:
            continue
        try:
            r = _importar_aba(mes, db)
            resultados.append(r)
        except Exception as e:
            resultados.append({"mes": MESES_NOMES[mes], "erro": str(e)})
    return resultados


# ── Helpers de serialização ───────────────────────────────────
def _nf_to_dict(nf: NotaFiscal) -> dict:
    return {
        "id":              nf.id,
        "mes_ref":         nf.mes_ref,
        "mes_nome":        MESES_NOMES.get(nf.mes_ref, str(nf.mes_ref)),
        "cliente":         nf.cliente,
        "numero_nf":       nf.numero_nf,
        "data_vencimento": nf.data_vencimento.strftime("%d/%m/%Y") if nf.data_vencimento else None,
        "valor":           nf.valor,
        "forma_pagamento": nf.forma_pagamento,
        "status":          nf.status,
        "pago_em":         nf.pago_em.strftime("%d/%m/%Y") if nf.pago_em else None,
        "fonte_memo":      nf.fonte_memo,
        "tipo_match":      nf.tipo_match,
        "cr_amount":       nf.cr_amount,
    }

def _inv_result(inv: dict) -> dict:
    return {
        "id":       inv["id"],
        "cliente":  inv["cliente"],
        "nota":     inv["nota"],
        "valor":    inv["valor"],
        "is_boleto": inv["is_boleto"],
        "status":   "PAGO" if inv["fonte"] else "ABERTO",
        "tipo":     inv.get("tipo"),
        "pago_em":  inv["pago_em"].strftime("%d/%m/%Y") if inv.get("pago_em") else None,
        "fonte":    inv.get("fonte", "")[:60] if inv.get("fonte") else None,
    }

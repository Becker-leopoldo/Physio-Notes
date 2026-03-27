"""
Conciliacao de Faturamento — UP IT
Executa: py conciliacao.py
Selecione o mes no menu interativo.

Logica de matching:
  1. Valor exato + cliente identificado no MEMO/CNPJ
  2. Valor exato sem identificar cliente
  3. Soma de NFs do mesmo grupo de pagador
"""

import re
import json
import unicodedata
from datetime import datetime
from itertools import combinations
from pathlib import Path
import gspread

# ── Configuracao ──────────────────────────────────────────────
OFX_DIR      = Path("extratos")   # todos os .ofx desta pasta sao lidos automaticamente
CREDS_FILE   = "credenciais.json"
SHEET_URL    = "https://docs.google.com/spreadsheets/d/1M03peFMUukZuS0k7I44to3OUap-BrhaiHL-8f8PAZUo/edit?usp=sharing"
ALIASES_FILE = Path("aliases.json")
FITIDS_FILE  = Path("fitids_usados.json")  # FITID → mes que ja consumiu esse credito

# Meses disponiveis — adicionar novos conforme planilha for criada
MESES = [
    {"num": 1,  "nome": "Janeiro",   "sheet": "Receitas Janeiro",   "start": datetime(2026,  1, 10), "end": datetime(2026,  2, 28)},
    {"num": 2,  "nome": "Fevereiro", "sheet": "Receitas Fevereiro",  "start": datetime(2026,  2, 10), "end": datetime(2026,  3, 31)},
    {"num": 3,  "nome": "Marco",     "sheet": "Receitas Março",      "start": datetime(2026,  3, 10), "end": datetime(2026,  4, 30)},
    {"num": 4,  "nome": "Abril",     "sheet": "Receitas Abril",      "start": datetime(2026,  4, 10), "end": datetime(2026,  5, 31)},
    {"num": 5,  "nome": "Maio",      "sheet": "Receitas Maio",       "start": datetime(2026,  5, 10), "end": datetime(2026,  6, 30)},
    {"num": 6,  "nome": "Junho",     "sheet": "Receitas Junho",      "start": datetime(2026,  6, 10), "end": datetime(2026,  7, 31)},
    {"num": 7,  "nome": "Julho",     "sheet": "Receitas Julho",      "start": datetime(2026,  7, 10), "end": datetime(2026,  8, 31)},
    {"num": 8,  "nome": "Agosto",    "sheet": "Receitas Agosto",     "start": datetime(2026,  8, 10), "end": datetime(2026,  9, 30)},
    {"num": 9,  "nome": "Setembro",  "sheet": "Receitas Setembro",   "start": datetime(2026,  9, 10), "end": datetime(2026, 10, 31)},
    {"num": 10, "nome": "Outubro",   "sheet": "Receitas Outubro",    "start": datetime(2026, 10, 10), "end": datetime(2026, 11, 30)},
    {"num": 11, "nome": "Novembro",  "sheet": "Receitas Novembro",   "start": datetime(2026, 11, 10), "end": datetime(2026, 12, 31)},
    {"num": 12, "nome": "Dezembro",  "sheet": "Receitas Dezembro",   "start": datetime(2026, 12, 10), "end": datetime(2027,  1, 31)},
]

# Colunas na planilha (1-based)
COL_STATUS    = 4   # D
COL_EXTRATO   = 5   # E
COL_DATA_PGTO = 6   # F
COL_VALOR_CR  = 7   # G
COL_TIPO      = 8   # H
COL_FORMA     = 9   # I
COL_NOTA      = 11  # K
COL_VALOR_NF  = 17  # Q

# Clientes que pagam juntos (mesmo remetente bancario)
PAYER_GROUPS = {
    "REDE IMPAR":       "impar",
    "DASA":             "impar",
    "SAINT GOBAIN":     "saint_gobain",
    "SAINT GOBAIN PPC": "saint_gobain",
}

# Aliases base (fallback hardcoded)
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


# ── Helpers ───────────────────────────────────────────────────
def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", " ", t.lower()).strip()

def close(a: float, b: float) -> bool:
    return abs(a - b) <= 0.50

def extrair_cnpj(texto: str) -> str | None:
    m = re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", texto)
    return m.group(0) if m else None

def nome_extrato(memo: str) -> str:
    skip = {"RECEBIMENTOS", "PIX", "RECEBIDO", "TED", "RECEBIDA", "ENTRADA", "SISPAG"}
    parts = [p for p in memo.split() if p.upper() not in skip and len(p) > 2]
    return " ".join(parts[:8])


# ── Aliases persistentes (CNPJ → cliente) ────────────────────
def load_aliases() -> dict:
    aliases = dict(BASE_ALIASES)
    if ALIASES_FILE.exists():
        saved = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
        aliases.update(saved)
        print(f"  {len(saved)} CNPJs conhecidos carregados")
    return aliases

def save_aliases(invoices: list[dict]):
    existing = {}
    if ALIASES_FILE.exists():
        existing = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
    novos = 0
    for inv in invoices:
        if not inv["fonte"]:
            continue
        cnpj = extrair_cnpj(inv["fonte"])
        if cnpj and cnpj not in existing:
            existing[cnpj] = inv["cliente"]
            novos += 1
    if novos:
        ALIASES_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {novos} novo(s) CNPJ(s) aprendido(s) e salvos")


# ── FITIDs utilizados (evita reutilizar credito em outro mes) ─
def load_fitids_usados() -> dict:
    """Retorna {fitid: nome_mes} dos creditos ja consumidos."""
    if FITIDS_FILE.exists():
        return json.loads(FITIDS_FILE.read_text(encoding="utf-8"))
    return {}

def save_fitids_usados(invoices: list[dict], credits: list[dict], mes_nome: str):
    """Salva FITIDs dos creditos matched neste mes."""
    existing = load_fitids_usados()
    novos = 0
    for cr in credits:
        if cr["used"] and cr["fitid"] and cr["fitid"] not in existing:
            existing[cr["fitid"]] = mes_nome
            novos += 1
    if novos:
        FITIDS_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {novos} FITID(s) registrado(s) como utilizados")


# ── 1. OFX ───────────────────────────────────────────────────
def load_credits(start: datetime, end: datetime, fitids_usados: dict) -> list[dict]:
    ofx_files = sorted(OFX_DIR.glob("*.ofx"))
    if not ofx_files:
        raise FileNotFoundError(f"Nenhum arquivo .ofx encontrado em '{OFX_DIR}/'")
    print(f"  {len(ofx_files)} arquivo(s) OFX encontrado(s): {[f.name for f in ofx_files]}")

    seen_fitids = set()   # deduplicacao entre arquivos OFX
    credits = []
    for ofx_path in ofx_files:
        with open(ofx_path, encoding="cp1252", errors="replace") as f:
            content = f.read()
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
            if not (start <= date <= end):
                continue
            memo = fld("MEMO")
            if any(p in norm(memo) for p in MEMO_IGNORE):
                continue
            fitid = fld("FITID")
            if fitid and fitid in seen_fitids:
                continue   # duplicata entre arquivos OFX
            if fitid and fitid in fitids_usados:
                print(f"  [FITID {fitid}] ja utilizado em {fitids_usados[fitid]} — ignorado")
                continue
            if fitid:
                seen_fitids.add(fitid)
            credits.append({
                "date": date, "amount": amount, "memo": memo,
                "memo_norm": norm(memo), "cnpj": extrair_cnpj(memo),
                "fitid": fitid, "used": False,
            })
    return sorted(credits, key=lambda x: x["date"])


# ── 2. Google Sheets → invoices ───────────────────────────────
def load_invoices(rows: list[list]) -> list[dict]:
    invoices = []
    for i, row in enumerate(rows, start=2):
        def col(n):
            return row[n - 1] if n - 1 < len(row) else ""

        if not col(1):
            continue
        cliente = str(col(2)).strip()
        forma   = str(col(9)).strip().upper()
        nota    = col(11)
        venc    = col(13)
        valor_s = col(17)

        is_boleto = (forma == "BOLETO")
        try:
            float(cliente)
            continue
        except (ValueError, TypeError):
            pass
        if not cliente or not valor_s:
            continue
        try:
            valor = round(float(re.sub(r"[R$,\s]", "", str(valor_s))), 2)
        except (ValueError, TypeError):
            continue
        if valor <= 0:
            continue

        data_venc = None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                data_venc = datetime.strptime(str(venc)[:10], fmt)
                break
            except (ValueError, TypeError):
                continue

        invoices.append({
            "sheet_row": i, "cliente": cliente, "nota": nota,
            "data_venc": data_venc, "valor": valor,
            "is_boleto": is_boleto,
            "pago_em": None, "fonte": None, "tipo": None, "cr_amount": None,
        })
    return invoices


# ── 3b. Matching boletos iugu ─────────────────────────────────
IUGU_MEMO_KEY = "up solu"   # "UP SOLUCOES EM TECNOLOGIA..." aparece truncado no OFX
IUGU_TAXA_MAX = 10.00       # teto de taxa por boleto (sanidade)

def match_boletos_iugu(boletos: list[dict], credits: list[dict]) -> tuple:
    """
    Logica:  soma_bruto_boletos - credito_iugu = taxa_cobrada
    Condicao: taxa > 0 e taxa/boleto <= IUGU_TAXA_MAX
    Validacao e pela matematica — nao pelo mes do credito.
    Retorna (credito_usado, taxa_total) ou (None, None).
    """
    if not boletos:
        return None, None

    bruto = round(sum(b["valor"] for b in boletos), 2)

    iugu_crs = [cr for cr in credits
                if not cr["used"] and IUGU_MEMO_KEY in cr["memo_norm"]]
    if not iugu_crs:
        print("  [iugu] Nenhum credito 'UP SOLUCOES' encontrado na janela")
        return None, None

    # Coleta todos os combos validos (ate 4 creditos) e escolhe o de data mais antiga
    # Isso garante que creditos do proprio mes tenham prioridade sobre os do mes seguinte
    validos = []
    for r in range(1, min(len(iugu_crs) + 1, 5)):
        for combo in combinations(iugu_crs, r):
            soma = round(sum(c["amount"] for c in combo), 2)
            taxa = round(bruto - soma, 2)
            taxa_unit = taxa / len(boletos)
            if taxa > 0 and taxa_unit <= IUGU_TAXA_MAX:
                validos.append((list(combo), taxa))

    if validos:
        # Prefere o combo cujo credito mais recente e o mais antigo possivel
        validos.sort(key=lambda x: max(c["date"] for c in x[0]))
        combo, taxa = validos[0]
        data_ref = max(c["date"] for c in combo)
        datas = "+".join(c["date"].strftime("%d/%m") for c in combo)
        soma = round(sum(c["amount"] for c in combo), 2)
        print(f"  [iugu] {len(combo)} credito(s): R${soma:,.2f} ({datas})")
        print(f"  [iugu] Bruto: R${bruto:,.2f} | Taxa: R${taxa:,.2f} (R${taxa/len(boletos):.2f}/boleto)")
        for b in boletos:
            b["pago_em"]   = data_ref
            b["fonte"]     = combo[0]["memo"]
            b["tipo"]      = "iugu Boleto"
            b["cr_amount"] = b["valor"]
        for c in combo:
            c["used"] = True
        return combo[0], taxa

    print(f"  [iugu] Nenhum credito (ou combinacao) bate com bruto R${bruto:,.2f}")
    for iugu_cr in iugu_crs:
        taxa = round(bruto - iugu_cr["amount"], 2)
        print(f"    candidato: R${iugu_cr['amount']:,.2f} em {iugu_cr['date'].strftime('%d/%m/%Y')} -> taxa seria R${taxa:,.2f}")
    return None, None


# ── 3. Matching ───────────────────────────────────────────────
def run_match(invoices: list[dict], credits: list[dict], aliases: dict):

    def client_match(cr, cliente: str) -> bool:
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
        cr["used"] = True

    # Passo 1: valor exato + cliente identificado
    for inv in invoices:
        if inv["fonte"] or inv.get("is_boleto"): continue
        for cr in credits:
            if cr["used"] or not close(cr["amount"], inv["valor"]): continue
            if client_match(cr, inv["cliente"]):
                mark(inv, cr, "Valor exato")
                break

    # Passo 2: valor exato sem identificar cliente
    for inv in invoices:
        if inv["fonte"] or inv.get("is_boleto"): continue
        for cr in credits:
            if cr["used"] or not close(cr["amount"], inv["valor"]): continue
            mark(inv, cr, "Valor exato (sem id. cliente)")
            break

    # Passo 3: soma de NFs — mesmo grupo de pagador
    groups: dict[str, list] = {}
    for inv in invoices:
        if inv["fonte"] or inv.get("is_boleto"): continue
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


# ── 4. Escrever no Google Sheets ─────────────────────────────
def escrever_sheets(ws, all_invoices: list[dict]):
    updates = []
    for inv in all_invoices:
        r = inv["sheet_row"]
        if inv["fonte"]:
            updates.append({"range": f"D{r}", "values": [["PAGO"]]})
            updates.append({"range": f"E{r}", "values": [[nome_extrato(inv["fonte"])]]})
            updates.append({"range": f"F{r}", "values": [[inv["pago_em"].strftime("%d/%m/%Y")]]})
            updates.append({"range": f"G{r}", "values": [[inv["cr_amount"]]]})
            updates.append({"range": f"H{r}", "values": [[inv["tipo"]]]})
        else:
            updates.append({"range": f"D{r}", "values": [["ABERTO"]]})
            updates.append({"range": f"E{r}:H{r}", "values": [["", "", "", ""]]})
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
        print(f"  {len(updates)} celulas atualizadas no Sheets")


# ── Menu interativo ───────────────────────────────────────────
def selecionar_mes() -> dict:
    print("=" * 45)
    print("  CONCILIACAO DE FATURAMENTO — UP IT 2026")
    print("=" * 45)
    print("  Selecione o mes:\n")
    for m in MESES:
        print(f"    {m['num']:>2}. {m['nome']}")
    print("     0. Sair")
    print()
    while True:
        try:
            escolha = int(input("  Digite o numero do mes: ").strip())
        except (ValueError, EOFError):
            print("  Entrada invalida.")
            continue
        if escolha == 0:
            print("  Saindo.")
            exit(0)
        cfg = next((m for m in MESES if m["num"] == escolha), None)
        if cfg:
            return cfg
        print(f"  Opcao invalida. Digite entre 1 e {len(MESES)} ou 0 para sair.")


# ── MAIN ─────────────────────────────────────────────────────
if __name__ == "__main__":
    cfg = selecionar_mes()
    print()
    print(f"=== {cfg['nome'].upper()} 2026 ===")
    print(f"Janela: {cfg['start'].strftime('%d/%m/%Y')} ate {cfg['end'].strftime('%d/%m/%Y')}")

    print("\nCarregando aliases...")
    aliases = load_aliases()

    print("Carregando FITIDs ja utilizados...")
    fitids_usados = load_fitids_usados()
    print(f"  {len(fitids_usados)} credito(s) ja consumidos em meses anteriores")

    print("Carregando creditos OFX...")
    credits = load_credits(cfg["start"], cfg["end"], fitids_usados)
    print(f"  {len(credits)} creditos disponiveis na janela")

    print("Conectando ao Google Sheets...")
    gc = gspread.service_account(filename=CREDS_FILE)
    sh = gc.open_by_url(SHEET_URL)
    ws = sh.worksheet(cfg["sheet"])
    print(f"  Aba: {cfg['sheet']}")

    print("Lendo NFs...")
    rows        = ws.get_all_values()
    all_inv     = load_invoices(rows[1:])
    invoices    = [inv for inv in all_inv if not inv["is_boleto"]]
    boletos     = [inv for inv in all_inv if inv["is_boleto"]]
    print(f"  {len(invoices)} NFs (TED/PIX) | {len(boletos)} boletos iugu")

    print("Rodando matching NFs...")
    run_match(invoices, credits, aliases)

    print("Rodando matching boletos iugu...")
    iugu_cr, taxa_iugu = match_boletos_iugu(boletos, credits)

    # Resultado NFs
    matched_nf   = [inv for inv in invoices if inv["fonte"]]
    sem_match_nf = [inv for inv in invoices if not inv["fonte"]]
    matched_bol  = [b for b in boletos if b["fonte"]]

    print(f"\n--- NFs (TED/PIX): {len(matched_nf)}/{len(invoices)} conciliadas ---")
    for inv in invoices:
        status = f"OK [{inv['tipo']}]" if inv["fonte"] else "-- SEM MATCH"
        fonte  = (" >> " + inv["fonte"][:45]) if inv["fonte"] else ""
        print(f"  NF {str(inv['nota']):6}  {inv['cliente']:<38}  R${inv['valor']:>10,.2f}  {status}{fonte}")

    print(f"\n--- Boletos iugu: {len(matched_bol)}/{len(boletos)} conciliados ---")
    for b in boletos:
        status = f"OK [iugu Boleto]" if b["fonte"] else "-- SEM MATCH"
        print(f"  NF {str(b['nota']):6}  {b['cliente']:<38}  R${b['valor']:>10,.2f}  {status}")
    if taxa_iugu is not None:
        print(f"  Taxa iugu cobrada: R${taxa_iugu:,.2f} ({taxa_iugu/len(boletos):.2f}/boleto)")

    print("\nAprendendo aliases...")
    save_aliases(invoices)

    print("Registrando FITIDs utilizados...")
    save_fitids_usados(all_inv, credits, cfg["nome"])

    print("Escrevendo no Google Sheets...")
    escrever_sheets(ws, all_inv)
    print("\nConcluido!")

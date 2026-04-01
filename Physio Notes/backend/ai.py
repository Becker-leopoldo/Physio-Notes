import os
import json
import re
import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = "claude-haiku-4-5-20251001"

# Preço por 1M tokens (USD)
MODEL_PRICING = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-haiku-3-5-20241022": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00},
}


def _calcular_custo(modelo: str, input_tokens: int, output_tokens: int) -> float:
    preco = MODEL_PRICING.get(modelo, {"input": 1.00, "output": 5.00})
    return (input_tokens / 1_000_000 * preco["input"]) + (output_tokens / 1_000_000 * preco["output"])


def _registrar(tipo: str, message) -> None:
    try:
        import database as db
        u = message.usage
        custo = _calcular_custo(message.model, u.input_tokens, u.output_tokens)
        db.registrar_uso(tipo, message.model, u.input_tokens, u.output_tokens, custo)
    except Exception:
        pass  # billing nunca deve quebrar o fluxo principal


async def consolidar_sessao(transcricoes: list[str]) -> dict:
    """
    Recebe lista de transcrições da sessão de fisioterapia.
    Retorna dict com: nota (nota clínica profissional em texto corrido).
    """
    transcricao_completa = "\n\n".join(
        f"[Trecho {i + 1}]: {t}" for i, t in enumerate(transcricoes)
    )

    prompt = f"""Você é um assistente clínico de fisioterapia. A seguir estão transcrições brutas de áudio de uma sessão — a fala é informal, coloquial, com hesitações e repetições normais de conversa.

Sua tarefa: transformar essa fala informal em uma nota clínica profissional em texto corrido, em português.

Regras:
- Elimine vícios de linguagem ("é", "aí", "né", "tipo", "então"), repetições e hesitações
- Escreva em terceira pessoa (ex: "Paciente relata...", "Foi realizado...", "Observou-se...")
- Use terminologia fisioterapêutica adequada quando possível
- Mantenha TODOS os fatos clínicos mencionados, mesmo que ditos informalmente
- Texto corrido, sem títulos, sem listas, sem formatação

Responda APENAS com o texto da nota clínica.

Transcrições:
{transcricao_completa}"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("consolidar_sessao", message)

    nota = message.content[0].text.strip()
    return {"nota": nota}


def _bloco_anamnese(paciente: dict | None) -> str:
    if not paciente or not paciente.get("anamnese"):
        return ""
    return f"ANAMNESE INICIAL (registrada no cadastro do paciente):\n{paciente['anamnese']}\n\n---\n\n"


async def resumir_historico(
    historico: list[dict],
    paciente: dict | None = None,
    documentos: list[dict] | None = None,
) -> str:
    """
    Recebe lista de sessões consolidadas, dados do paciente e documentos (PDFs).
    Retorna relatório clínico formal baseado EXCLUSIVAMENTE nos dados fornecidos.
    """
    anamnese_bloco = _bloco_anamnese(paciente)
    if not historico and not anamnese_bloco and not documentos:
        return "Nenhum histórico de sessões encontrado para este paciente."

    sessoes_texto = []
    for i, s in enumerate(historico):
        partes = [f"Sessão {i + 1} — {s.get('data', 'data não informada')}"]
        nota = s.get("nota") or s.get("conduta") or s.get("queixa")
        if nota:
            partes.append(nota)
        sessoes_texto.append("\n".join(partes))

    historico_formatado = "\n\n---\n\n".join(sessoes_texto) if sessoes_texto else "Nenhuma sessão encerrada registrada."

    docs_bloco = ""
    if documentos:
        docs_partes = []
        for d in documentos:
            if d.get("resumo_ia"):
                docs_partes.append(f"Documento: {d.get('nome_original', 'sem nome')}\n{d['resumo_ia']}")
        if docs_partes:
            docs_bloco = "DOCUMENTOS CLÍNICOS ANEXADOS (laudos, exames, prontuários):\n" + "\n\n---\n\n".join(docs_partes) + "\n\n"

    prompt = f"""Você é um fisioterapeuta clínico elaborando um relatório formal para o CREFITO.

REGRAS INVIOLÁVEIS:
- Baseie-se EXCLUSIVAMENTE nas informações fornecidas abaixo
- Não invente, não infira, não suponha nada que não esteja explicitamente nos dados
- Se uma informação não constar nos dados, não a mencione
- Use linguagem técnica formal, em terceira pessoa
- Texto corrido em parágrafos, sem bullet points, sem títulos internos

O relatório deve cobrir (apenas com base nos dados disponíveis):
1. Identificação e histórico inicial do paciente
2. Evolução clínica ao longo das sessões
3. Condutas e técnicas aplicadas
4. Achados de exames ou laudos (se houver documentos)
5. Situação atual e tendências observadas

{anamnese_bloco}{docs_bloco}NOTAS DE SESSÕES:
{historico_formatado}"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("resumir_historico", message)

    return message.content[0].text.strip()


async def extrair_dados_paciente(transcricao: str) -> dict:
    """
    Extrai dados estruturados do paciente a partir de uma transcrição de áudio.
    Retorna dict com: nome, data_nascimento (formato YYYY-MM-DD ou None), anamnese.
    """
    prompt = f"""Você é um assistente de fisioterapia. A fisioterapeuta gravou um áudio introduzindo um novo paciente.

Extraia as seguintes informações da transcrição e retorne APENAS um JSON válido com estas chaves:

{{
  "nome": "Nome completo do paciente",
  "data_nascimento": "Data de nascimento no formato YYYY-MM-DD (ou null se não mencionada)",
  "anamnese": "Queixa principal, histórico clínico e qualquer outra informação clínica relevante mencionada"
}}

Se o nome não for mencionado, use null. Se a data não for clara, use null. Capture tudo de clínico relevante em anamnese.
Responda APENAS com o JSON, sem texto adicional.

Transcrição:
{transcricao}"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("extrair_dados_paciente", message)

    raw_text = message.content[0].text.strip()
    json_match = re.search(r"\{[\s\S]*\}", raw_text)
    if json_match:
        raw_text = json_match.group(0)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        result = {"nome": None, "data_nascimento": None, "anamnese": raw_text}

    for chave in ("nome", "data_nascimento", "anamnese"):
        result.setdefault(chave, None)

    return result


async def extrair_dados_pacote(transcricao: str) -> dict:
    """
    Extrai dados de um pacote de sessões a partir de uma transcrição de áudio.
    Retorna dict com: total_sessoes (int), valor_pago (float|None),
    data_pagamento (str YYYY-MM-DD|None), descricao (str|None).
    """
    hoje = __import__("datetime").date.today().isoformat()
    prompt = f"""Você é um assistente de fisioterapia. A fisioterapeuta gravou um áudio registrando um novo pacote de sessões para um paciente.

Extraia as seguintes informações da transcrição e retorne APENAS um JSON válido com estas chaves:

{{
  "total_sessoes": <número inteiro de sessões do pacote, obrigatório>,
  "valor_pago": <valor em reais como número decimal, ou null se não mencionado>,
  "data_pagamento": <data do pagamento no formato YYYY-MM-DD, ou null se não mencionada. Hoje é {hoje}. Interprete expressões como "hoje", "ontem", "semana passada".>,
  "descricao": <descrição breve do pacote, ou null se não houver>
}}

Se a quantidade de sessões não for mencionada, use null para total_sessoes.
Responda APENAS com o JSON, sem texto adicional.

Transcrição:
{transcricao}"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("extrair_dados_pacote", message)

    raw_text = message.content[0].text.strip()
    json_match = re.search(r"\{[\s\S]*\}", raw_text)
    if json_match:
        raw_text = json_match.group(0)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        result = {"total_sessoes": None, "valor_pago": None, "data_pagamento": None, "descricao": None}

    for chave in ("total_sessoes", "valor_pago", "data_pagamento", "descricao"):
        result.setdefault(chave, None)

    return result


async def detectar_procedimentos_extras(transcricao_completa: str, nota_clinica: str | None) -> list[dict]:
    """
    Analisa a transcrição bruta e a nota clínica da sessão em busca de
    procedimentos extras cobrados além do pacote.
    Retorna lista de {descricao, valor} — valor pode ser None.
    """
    nota_bloco = f"\n\nNOTA CLÍNICA CONSOLIDADA:\n{nota_clinica}" if nota_clinica else ""
    prompt = f"""Você é um assistente de faturamento de clínica de fisioterapia.

Analise o texto abaixo (transcrição de sessão clínica{' e nota consolidada' if nota_clinica else ''}) e identifique APENAS cobranças ou procedimentos extras que foram realizados ALÉM do pacote de sessões padrão.

Exemplos do que deve ser identificado:
- "fizemos laser que custa 200 reais a mais"
- "cobrei 50 de eletroterapia adicional"
- "acupuntura, 80 reais extra"
- "ventosaterapia por fora do pacote"

NÃO inclua: a própria sessão de fisioterapia, consultas já cobertas pelo pacote, ou itens sem indicação clara de cobrança extra.

Retorne APENAS um JSON com uma lista. Se não houver extras, retorne lista vazia:

[
  {{"descricao": "nome do procedimento", "valor": <número decimal ou null>}},
  ...
]

TRANSCRIÇÃO:
{transcricao_completa}{nota_bloco}"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("extrair_procedimento", message)

    raw_text = message.content[0].text.strip()
    json_match = re.search(r"\[[\s\S]*\]", raw_text)
    if json_match:
        raw_text = json_match.group(0)

    try:
        result = json.loads(raw_text)
        if not isinstance(result, list):
            result = []
    except json.JSONDecodeError:
        result = []

    # normaliza cada item
    clean = []
    for item in result:
        if isinstance(item, dict) and item.get("descricao"):
            clean.append({
                "descricao": str(item["descricao"]),
                "valor": float(item["valor"]) if item.get("valor") else None,
            })
    return clean


async def extrair_procedimento(transcricao: str) -> dict:
    """
    Extrai dados de um procedimento extra a partir de transcrição.
    Retorna dict com: descricao (str), valor (float|None).
    """
    prompt = f"""Você é um assistente de fisioterapia. A fisioterapeuta gravou um áudio mencionando um procedimento ou serviço extra cobrado nesta sessão.

Extraia as seguintes informações e retorne APENAS um JSON válido:

{{
  "descricao": "Nome ou descrição do procedimento/serviço cobrado",
  "valor": <valor em reais como número decimal, ou null se não mencionado>
}}

Exemplos de entrada: "cobrei 50 de eletroterapia", "acupuntura, 80 reais", "ventosaterapia sem cobrança adicional"
Responda APENAS com o JSON, sem texto adicional.

Transcrição:
{transcricao}"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=128,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("extrair_procedimento", message)

    raw_text = message.content[0].text.strip()
    json_match = re.search(r"\{[\s\S]*\}", raw_text)
    if json_match:
        raw_text = json_match.group(0)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        result = {"descricao": raw_text, "valor": None}

    result.setdefault("descricao", None)
    result.setdefault("valor", None)
    return result


async def responder_pergunta(pergunta: str, historico: list[dict], paciente: dict | None = None) -> str:
    """
    Responde pergunta da fisioterapeuta com base no histórico do paciente.
    """
    anamnese_bloco = _bloco_anamnese(paciente)
    if not historico and not anamnese_bloco:
        return "Não há histórico registrado para este paciente. Registre algumas sessões antes de fazer perguntas."

    sessoes_texto = []
    for i, s in enumerate(historico):
        partes = [f"Sessão {i + 1} — {s.get('data', 'data não informada')} (status: {s.get('status', '')})"]
        nota = s.get("nota") or s.get("conduta") or s.get("queixa")
        if nota:
            partes.append(nota)
        sessoes_texto.append("\n".join(partes))

    historico_formatado = "\n\n---\n\n".join(sessoes_texto)

    prompt = f"""Você é um assistente clínico especializado em fisioterapia, ajudando uma fisioterapeuta a consultar o histórico de um paciente.

{anamnese_bloco}Histórico de sessões do paciente:
{historico_formatado}

---

Pergunta da fisioterapeuta: {pergunta}

Responda de forma direta, precisa e baseada apenas nas informações disponíveis.
Se a informação solicitada não estiver nos dados, informe isso claramente.
Responda em português."""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("responder_pergunta", message)

    return message.content[0].text.strip()


async def resumir_documento(texto: str) -> str:
    """Gera resumo clínico de um documento PDF enviado pelo fisioterapeuta."""
    prompt = f"""Você é um assistente clínico especializado em fisioterapia.
O documento abaixo é um prontuário, laudo, exame ou relatório médico de um paciente.

Faça um resumo clínico objetivo em português com:
- Diagnóstico ou queixa principal mencionada
- Achados clínicos relevantes (exames, laudos, medidas)
- Recomendações ou condutas indicadas no documento
- Qualquer informação que seja relevante para o tratamento fisioterapêutico

Seja direto e use linguagem clínica. Se o documento não contiver informações clínicas relevantes, informe isso.

Documento:
{texto[:12000]}"""  # limita para não exceder contexto

    message = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("resumir_documento", message)

    return message.content[0].text.strip()


async def complementar_anamnese(transcricao: str, anamnese_atual: str | None) -> str:
    """
    Recebe uma transcrição de áudio com novas informações de anamnese e a anamnese
    atual do paciente. Retorna a anamnese completa e atualizada em linguagem clínica,
    integrando os dados novos com os existentes de forma coerente e técnica.
    """
    bloco_atual = f"\n\nANAMNESE ATUAL DO PACIENTE:\n{anamnese_atual}" if anamnese_atual else "\n\n(Paciente ainda não possui anamnese registrada.)"

    prompt = f"""Você é um fisioterapeuta clínico experiente. A profissional gravou um áudio com novas informações ou complementos sobre a anamnese de um paciente.

Sua tarefa é integrar as novas informações com a anamnese existente e retornar a anamnese COMPLETA e ATUALIZADA em linguagem clínica técnica.

Regras:
- Mantenha todas as informações anteriores relevantes
- Incorpore as novas informações de forma coerente
- Use linguagem clínica objetiva (fisioterapia)
- Organize por tópicos quando houver múltiplas informações (queixa principal, histórico, comorbidades, medicamentos, etc.)
- Não repita informações redundantes
- Retorne APENAS o texto da anamnese atualizada, sem introduções ou explicações{bloco_atual}

NOVA INFORMAÇÃO GRAVADA (transcrição):
{transcricao}"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("complementar_anamnese", message)

    return message.content[0].text.strip()

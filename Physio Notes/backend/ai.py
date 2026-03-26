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

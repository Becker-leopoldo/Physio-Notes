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
    Retorna dict com: queixa, evolucao, conduta, observacoes, proximos_passos.
    """
    transcricao_completa = "\n\n".join(
        f"[Trecho {i + 1}]: {t}" for i, t in enumerate(transcricoes)
    )

    prompt = f"""Você é um assistente especializado em fisioterapia. A seguir estão as transcrições de áudio de uma sessão de fisioterapia.

Analise o conteúdo e extraia as informações clínicas relevantes no seguinte formato JSON:

{{
  "queixa": "Queixa principal do paciente nesta sessão (o que o paciente relatou sentir, suas dores e limitações)",
  "evolucao": "Evolução clínica observada em relação às sessões anteriores ou ao início do tratamento",
  "conduta": "Condutas e técnicas aplicadas durante a sessão (exercícios, manobras, terapias realizadas)",
  "observacoes": "Observações relevantes do fisioterapeuta sobre o paciente, comportamento, aderência, postura etc.",
  "proximos_passos": "Orientações para casa, próximos exercícios, metas para próximas sessões"
}}

Se alguma informação não estiver presente na transcrição, use null para esse campo.
Responda APENAS com o JSON válido, sem texto adicional.

Transcrições da sessão:
{transcricao_completa}"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("consolidar_sessao", message)

    raw_text = message.content[0].text.strip()

    # Extrai JSON mesmo que venha envolto em markdown code block
    json_match = re.search(r"\{[\s\S]*\}", raw_text)
    if json_match:
        raw_text = json_match.group(0)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        # Fallback seguro se Claude não retornar JSON válido
        result = {
            "queixa": None,
            "evolucao": None,
            "conduta": raw_text,
            "observacoes": None,
            "proximos_passos": None,
        }

    # Garante que todas as chaves esperadas estejam presentes
    for chave in ("queixa", "evolucao", "conduta", "observacoes", "proximos_passos"):
        result.setdefault(chave, None)

    return result


def _bloco_anamnese(paciente: dict | None) -> str:
    if not paciente or not paciente.get("anamnese"):
        return ""
    return f"ANAMNESE INICIAL (registrada no cadastro do paciente):\n{paciente['anamnese']}\n\n---\n\n"


async def resumir_historico(historico: list[dict], paciente: dict | None = None) -> str:
    """
    Recebe lista de sessões consolidadas do paciente.
    Retorna resumo narrativo em texto corrido, em português.
    """
    anamnese_bloco = _bloco_anamnese(paciente)
    if not historico and not anamnese_bloco:
        return "Nenhum histórico de sessões encontrado para este paciente."

    sessoes_texto = []
    for i, s in enumerate(historico):
        partes = [f"Sessão {i + 1} — {s.get('data', 'data não informada')}"]
        if s.get("queixa"):
            partes.append(f"Queixa: {s['queixa']}")
        if s.get("evolucao"):
            partes.append(f"Evolução: {s['evolucao']}")
        if s.get("conduta"):
            partes.append(f"Conduta: {s['conduta']}")
        if s.get("observacoes") or s.get("consolidado_observacoes"):
            partes.append(f"Observações: {s.get('observacoes') or s.get('consolidado_observacoes')}")
        if s.get("proximos_passos"):
            partes.append(f"Próximos passos: {s['proximos_passos']}")
        sessoes_texto.append("\n".join(partes))

    historico_formatado = "\n\n---\n\n".join(sessoes_texto)

    prompt = f"""Você é um assistente clínico especializado em fisioterapia.

Com base nos dados abaixo, escreva um resumo narrativo completo do paciente em português.
O resumo deve:
- Considerar a anamnese inicial como ponto de partida do tratamento
- Descrever a evolução geral do paciente ao longo das sessões
- Destacar os principais problemas tratados
- Mencionar as condutas mais utilizadas
- Indicar tendências de melhora ou dificuldades persistentes
- Ser escrito em linguagem clínica, mas clara e acessível
- Ter formato de texto corrido (parágrafos), sem bullet points

{anamnese_bloco}Histórico de sessões:
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
        if s.get("queixa"):
            partes.append(f"Queixa: {s['queixa']}")
        if s.get("evolucao"):
            partes.append(f"Evolução: {s['evolucao']}")
        if s.get("conduta"):
            partes.append(f"Conduta: {s['conduta']}")
        if s.get("observacoes") or s.get("consolidado_observacoes"):
            partes.append(f"Observações: {s.get('observacoes') or s.get('consolidado_observacoes')}")
        if s.get("proximos_passos"):
            partes.append(f"Próximos passos: {s['proximos_passos']}")
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

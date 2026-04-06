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


def _registrar(tipo: str, message, owner_email: str | None = None) -> None:
    try:
        import database as db
        u = message.usage
        custo = _calcular_custo(message.model, u.input_tokens, u.output_tokens)
        db.registrar_uso(tipo, message.model, u.input_tokens, u.output_tokens, custo, owner_email)
    except Exception:
        pass  # billing nunca deve quebrar o fluxo principal


async def consolidar_sessao(transcricoes: list[str], owner_email: str | None = None) -> dict:
    """
    Recebe lista de transcrições da sessão de fisioterapia.
    Retorna dict com: nota (nota clínica profissional em texto corrido).
    """
    transcricao_completa = "\n\n".join(
        f"[Trecho {i + 1}]: {t}" for i, t in enumerate(transcricoes)
    )

    prompt = f"""Você é um fisioterapeuta clínico experiente, com domínio completo de anatomia, biomecânica, reabilitação musculoesquelética, neurológica e respiratória, e dos jargões técnicos da fisioterapia brasileira. A seguir estão transcrições brutas de áudio de uma sessão — a fala é informal, coloquial, com hesitações e repetições normais de conversa.

Sua tarefa: transformar essa fala informal em uma nota clínica profissional em texto corrido, em português.

Regras:
- Elimine vícios de linguagem ("é", "aí", "né", "tipo", "então"), repetições e hesitações
- Escreva em terceira pessoa (ex: "Paciente relata...", "Foi realizado...", "Observou-se...")
- Interprete e converta corretamente jargões e abreviações clínicas da fisioterapia (ex: "DDC", "TENS", "FNP", "RPG", "PNF", "ESWT", "RICE", "ADM", "EVA", etc.)
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
    _registrar("consolidar_sessao", message, owner_email)

    nota = message.content[0].text.strip()
    return {"nota": nota}


def _bloco_anamnese(paciente: dict | None) -> str:
    if not paciente or not paciente.get("anamnese"):
        return ""
    return f"ANAMNESE INICIAL (registrada no cadastro do paciente):\n{paciente['anamnese']}\n\n---\n\n"


def _bloco_conduta(paciente: dict | None) -> str:
    if not paciente or not paciente.get("conduta_tratamento"):
        return ""
    return f"CONDUTA DE TRATAMENTO (plano terapêutico do paciente):\n{paciente['conduta_tratamento']}\n\n---\n\n"


async def resumir_historico(
    historico: list[dict],
    paciente: dict | None = None,
    documentos: list[dict] | None = None,
    owner_email: str | None = None,
    tipo: str = "completo",
) -> str:
    """
    Recebe lista de sessões consolidadas, dados do paciente e documentos (PDFs).
    tipo='resumido' → resumo clínico rápido (máx ~20 linhas)
    tipo='completo' → relatório clínico formal e detalhado
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

    if tipo == "resumido":
        prompt = f"""Você é um fisioterapeuta clínico experiente, com domínio completo de anatomia, biomecânica, reabilitação musculoesquelética, neurológica e respiratória, e dos jargões técnicos da fisioterapia brasileira. Elabore um RESUMO CLÍNICO RÁPIDO e OBJETIVO deste paciente — máximo 20 linhas no total.

REGRAS:
- Baseie-se EXCLUSIVAMENTE nas informações fornecidas
- Não invente nem infira nada além do que está nos dados
- Linguagem técnica direta, pode usar tópicos curtos
- Máximo 20 linhas. Seja objetivo — este texto é para um profissional de saúde ter uma visão rápida do quadro clínico

Estrutura sugerida (adapte conforme os dados disponíveis):
• Queixa principal / motivo do atendimento
• Histórico relevante (se houver)
• Técnicas e condutas aplicadas
• Evolução observada
• Situação atual

{anamnese_bloco}{docs_bloco}NOTAS DE SESSÕES:
{historico_formatado}"""
        max_tokens = 512
    else:
        prompt = f"""Você é um fisioterapeuta clínico experiente, com domínio completo de anatomia, biomecânica, reabilitação musculoesquelética, neurológica e respiratória, e dos jargões técnicos da fisioterapia brasileira, elaborando um relatório clínico detalhado.

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
        max_tokens = 2048

    message = await client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("resumir_historico", message, owner_email)

    return message.content[0].text.strip()


async def extrair_dados_paciente(transcricao: str, owner_email: str | None = None) -> dict:
    """
    Extrai dados cadastrais do paciente a partir de uma transcrição de áudio.
    Retorna dict com: nome, data_nascimento, cpf, endereco.
    Anamnese e conduta são registradas separadamente após o cadastro.
    """
    prompt = f"""Você é um fisioterapeuta clínico experiente, com domínio dos procedimentos, técnicas e terminologia da fisioterapia brasileira. A fisioterapeuta gravou um áudio cadastrando um novo paciente.

Extraia APENAS os dados cadastrais e retorne um JSON válido com estas chaves:

{{
  "nome": "Nome completo do paciente (ou null se não mencionado)",
  "data_nascimento": "Data de nascimento no formato YYYY-MM-DD (ou null se não mencionada)",
  "cpf": "CPF contendo apenas os 11 dígitos numéricos, sem pontuação (ou null se não mencionado)",
  "endereco": "Endereço completo — rua, número, bairro, cidade (ou null se não mencionado)"
}}

Não extraia informações clínicas — anamnese e conduta de tratamento serão registradas separadamente.
Responda APENAS com o JSON, sem texto adicional.

Transcrição:
{transcricao}"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("extrair_dados_paciente", message, owner_email)

    raw_text = message.content[0].text.strip()
    json_match = re.search(r"\{[\s\S]*\}", raw_text)
    if json_match:
        raw_text = json_match.group(0)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        result = {"nome": None, "data_nascimento": None, "cpf": None, "endereco": None}

    for chave in ("nome", "data_nascimento", "cpf", "endereco"):
        result.setdefault(chave, None)

    return result


async def extrair_dados_pacote(transcricao: str, owner_email: str | None = None) -> dict:
    """
    Extrai dados de um pacote de sessões a partir de uma transcrição de áudio.
    Retorna dict com: total_sessoes (int), valor_pago (float|None),
    data_pagamento (str YYYY-MM-DD|None), descricao (str|None).
    """
    hoje = __import__("datetime").date.today().isoformat()
    prompt = f"""Você é um fisioterapeuta clínico experiente, com domínio dos procedimentos, técnicas e terminologia da fisioterapia brasileira. A fisioterapeuta gravou um áudio registrando um novo pacote de sessões para um paciente.

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
    _registrar("extrair_dados_pacote", message, owner_email)

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


async def detectar_procedimentos_extras(transcricao_completa: str, nota_clinica: str | None, owner_email: str | None = None) -> list[dict]:
    """
    Analisa a transcrição bruta e a nota clínica da sessão em busca de
    procedimentos extras cobrados além do pacote.
    Retorna lista de {descricao, valor} — valor pode ser None.
    """
    nota_bloco = f"\n\nNOTA CLÍNICA CONSOLIDADA:\n{nota_clinica}" if nota_clinica else ""
    prompt = f"""Você é um fisioterapeuta clínico experiente que também gerencia o faturamento da clínica, conhecendo profundamente os procedimentos e técnicas da área.

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
    _registrar("extrair_procedimento", message, owner_email)

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

    clean = []
    for item in result:
        if isinstance(item, dict) and item.get("descricao"):
            clean.append({
                "descricao": str(item["descricao"]),
                "valor": float(item["valor"]) if item.get("valor") else None,
            })
    return clean


async def extrair_procedimento(transcricao: str, owner_email: str | None = None) -> dict:
    """
    Extrai dados de um procedimento extra a partir de transcrição.
    Retorna dict com: descricao (str), valor (float|None).
    """
    prompt = f"""Você é um fisioterapeuta clínico experiente, com domínio dos procedimentos, técnicas e terminologia da fisioterapia brasileira. A fisioterapeuta gravou um áudio mencionando um procedimento ou serviço extra cobrado nesta sessão.

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
    _registrar("extrair_procedimento", message, owner_email)

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


async def responder_pergunta(pergunta: str, historico: list[dict], paciente: dict | None = None, owner_email: str | None = None) -> str:
    """
    Responde pergunta da fisioterapeuta com base no histórico do paciente.
    """
    anamnese_bloco = _bloco_anamnese(paciente)
    conduta_bloco = _bloco_conduta(paciente)
    if not historico and not anamnese_bloco and not conduta_bloco:
        return "Não há histórico registrado para este paciente. Registre algumas sessões antes de fazer perguntas."

    sessoes_texto = []
    for i, s in enumerate(historico):
        partes = [f"Sessão {i + 1} — {s.get('data', 'data não informada')} (status: {s.get('status', '')})"]
        nota = s.get("nota") or s.get("conduta") or s.get("queixa")
        if nota:
            partes.append(nota)
        sessoes_texto.append("\n".join(partes))

    historico_formatado = "\n\n---\n\n".join(sessoes_texto) if sessoes_texto else "Nenhuma sessão encerrada registrada."

    prompt = f"""Você é um fisioterapeuta clínico experiente, com domínio completo de anatomia, biomecânica, reabilitação e dos jargões técnicos da fisioterapia brasileira, ajudando uma colega a consultar o histórico de um paciente.

{anamnese_bloco}{conduta_bloco}Histórico de sessões do paciente:
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
    _registrar("responder_pergunta", message, owner_email)

    return message.content[0].text.strip()


async def resumir_documento(texto: str, owner_email: str | None = None) -> str:
    """Gera resumo clínico de um documento PDF enviado pelo fisioterapeuta."""
    prompt = f"""Você é um fisioterapeuta clínico experiente, com domínio de anatomia, biomecânica, reabilitação e dos jargões técnicos da fisioterapia brasileira.
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
    _registrar("resumir_documento", message, owner_email)

    return message.content[0].text.strip()


async def complementar_anamnese(transcricao: str, anamnese_atual: str | None, owner_email: str | None = None) -> str:
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
- Organize por tópicos usando **NOME DO TÓPICO:** em negrito (queixa principal, histórico, comorbidades, medicamentos, etc.)
- NÃO inclua título geral como "# ANAMNESE CLÍNICA", "# ANAMNESE ATUALIZADA" ou qualquer cabeçalho com # — o título já é exibido pela interface
- Use listas com "- " para enumerações dentro dos tópicos
- Não repita informações redundantes
- Retorne APENAS o texto da anamnese atualizada, sem introduções ou explicações{bloco_atual}

NOVA INFORMAÇÃO GRAVADA (transcrição):
{transcricao}"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("complementar_anamnese", message, owner_email)

    return message.content[0].text.strip()


async def complementar_conduta(transcricao: str, conduta_atual: str | None, owner_email: str | None = None) -> str:
    """
    Recebe uma transcrição de áudio com informações de conduta de tratamento e a conduta
    atual do paciente. Retorna a conduta completa e atualizada em linguagem clínica.
    """
    bloco_atual = f"\n\nCONDUTA DE TRATAMENTO ATUAL:\n{conduta_atual}" if conduta_atual else "\n\n(Paciente ainda não possui conduta de tratamento registrada.)"

    prompt = f"""Você é um fisioterapeuta clínico experiente. A profissional gravou um áudio descrevendo a conduta de tratamento de um paciente.

Sua tarefa é integrar as novas informações com a conduta existente e retornar a conduta de tratamento COMPLETA e ATUALIZADA em linguagem clínica técnica.

Regras:
- Mantenha todas as informações anteriores relevantes
- Incorpore as novas informações de forma coerente
- Use linguagem clínica objetiva (fisioterapia)
- Organize por tópicos usando **NOME DO TÓPICO:** em negrito (objetivos, técnicas, frequência, evolução esperada, etc.)
- NÃO inclua título geral com # — o título já é exibido pela interface
- Use listas com "- " para enumerações dentro dos tópicos
- Não repita informações redundantes
- Retorne APENAS o texto da conduta atualizada, sem introduções ou explicações{bloco_atual}

NOVA INFORMAÇÃO GRAVADA (transcrição):
{transcricao}"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("complementar_conduta", message, owner_email)

    return message.content[0].text.strip()


async def sugerir_conduta(anamnese: str, owner_email: str | None = None) -> str:
    """
    Lê a anamnese do paciente e sugere uma conduta de tratamento fisioterapêutica.
    É uma sugestão — a fisioterapeuta decide se aceita ou modifica.
    """
    prompt = f"""Você é um fisioterapeuta clínico experiente. Com base na anamnese abaixo, elabore uma sugestão de conduta de tratamento fisioterapêutico.

Regras:
- Baseie-se ESTRITAMENTE nas informações da anamnese — não invente dados
- Use linguagem clínica técnica (fisioterapia)
- Organize por tópicos usando **NOME DO TÓPICO:** em negrito (ex: **Objetivos terapêuticos:**, **Técnicas propostas:**, **Frequência sugerida:**, **Evolução esperada:**)
- Use listas com "- " para enumerações dentro dos tópicos
- NÃO inclua título geral com # — o título já é exibido pela interface
- Retorne APENAS o texto da conduta sugerida, sem introduções ou explicações

ANAMNESE DO PACIENTE:
{anamnese}"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("sugerir_conduta", message, owner_email)

    return message.content[0].text.strip()


async def gerar_sugestao_paciente(
    anamnese: str,
    sessoes_recentes: list[dict],
    owner_email: str | None = None,
) -> dict:
    """
    Gera sugestão clínica estruturada com base na anamnese e nas últimas sessões
    (sliding window de até 8 sessões). Retorna dict com:
      reavaliacao, testes_fisioterapeuticos, exames_clinicos
    Cada campo é uma lista de strings (bullets).
    """
    sessoes_texto = []
    for i, s in enumerate(sessoes_recentes[:8]):
        nota = s.get("nota") or s.get("conduta") or s.get("queixa") or ""
        if nota:
            sessoes_texto.append(f"Sessão recente {i + 1} — {s.get('data', '')}:\n{nota}")

    historico_bloco = (
        "\n\n---\n\n".join(sessoes_texto)
        if sessoes_texto
        else "Nenhuma sessão encerrada registrada ainda."
    )

    prompt = f"""Você é um fisioterapeuta clínico experiente, com domínio de anatomia, biomecânica, reabilitação musculoesquelética e neurológica.

Com base na anamnese e nas sessões recentes abaixo, elabore sugestões clínicas objetivas para o próximo período de tratamento.

ANAMNESE DO PACIENTE:
{anamnese}

SESSÕES RECENTES (até 8 mais recentes):
{historico_bloco}

Retorne APENAS um JSON válido com esta estrutura (cada campo é uma lista de strings):
{{
  "reavaliacao": ["sugestão 1", "sugestão 2", ...],
  "testes_fisioterapeuticos": ["teste 1", "teste 2", ...],
  "exames_clinicos": ["exame 1", "exame 2", ...]
}}

Regras:
- Baseie-se EXCLUSIVAMENTE nas informações fornecidas — não invente dados
- Cada lista deve ter de 2 a 5 itens objetivos e específicos
- Use linguagem clínica técnica da fisioterapia brasileira
- Se não houver dados suficientes para uma seção, coloque ["Aguardando mais sessões para sugestão específica."]
- Responda APENAS com o JSON, sem texto adicional"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("gerar_sugestao_paciente", message, owner_email)

    raw_text = message.content[0].text.strip()
    json_match = re.search(r"\{[\s\S]*\}", raw_text)
    if json_match:
        raw_text = json_match.group(0)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        result = {}

    for chave in ("reavaliacao", "testes_fisioterapeuticos", "exames_clinicos"):
        if chave not in result or not isinstance(result[chave], list):
            result[chave] = ["Não foi possível gerar sugestão para este campo."]

    return result


async def formatar_anamnese_texto(texto: str, owner_email: str | None = None) -> str:
    """
    Recebe texto livre de anamnese e o reorganiza com tópicos
    no padrão **TÓPICO:** sem adicionar nem remover informações clínicas.
    """
    prompt = f"""Você é um fisioterapeuta clínico experiente. O profissional colou ou digitou manualmente o texto abaixo sobre a anamnese de um paciente.

Sua tarefa é APENAS reorganizar e formatar o texto, sem adicionar nem remover nenhuma informação clínica.

Regras:
- Mantenha TODAS as informações do texto original — não invente, não omita nada
- Organize por tópicos usando **NOME DO TÓPICO:** em negrito (queixa principal, histórico, comorbidades, medicamentos, antecedentes, etc.)
- Agrupe informações relacionadas no tópico mais adequado
- Use listas com "- " para enumerações dentro dos tópicos
- NÃO inclua título geral com # — o título já é exibido pela interface
- Retorne APENAS o texto reorganizado, sem introduções ou explicações

TEXTO ORIGINAL:
{texto}"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("formatar_anamnese_texto", message, owner_email)
    return message.content[0].text.strip()


async def formatar_conduta_texto(texto: str, owner_email: str | None = None) -> str:
    """
    Recebe texto livre de conduta de tratamento e o reorganiza com tópicos
    no padrão **TÓPICO:** sem adicionar nem remover informações clínicas.
    """
    prompt = f"""Você é um fisioterapeuta clínico experiente. O profissional digitou manualmente o texto abaixo sobre a conduta de tratamento de um paciente.

Sua tarefa é APENAS reorganizar e formatar o texto, sem adicionar nem remover nenhuma informação clínica.

Regras:
- Mantenha TODAS as informações do texto original — não invente, não omita nada
- Organize por tópicos usando **NOME DO TÓPICO:** em negrito (ex: **Objetivos terapêuticos:**, **Técnicas propostas:**, **Frequência sugerida:**, **Evolução esperada:**, **Observações:**)
- Agrupe informações relacionadas no tópico mais adequado
- Use listas com "- " para enumerações dentro dos tópicos
- NÃO inclua título geral com # — o título já é exibido pela interface
- Retorne APENAS o texto reorganizado, sem introduções ou explicações

TEXTO ORIGINAL:
{texto}"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("formatar_conduta_texto", message, owner_email)
    return message.content[0].text.strip()


async def sugestao_do_dia(
    anamnese: str,
    conduta: str | None,
    sessoes_recentes: list[dict],
    owner_email: str | None = None,
) -> dict:
    """
    Gera sugestão prática para a sessão de hoje com base na anamnese,
    conduta de tratamento e últimas 2-3 sessões.
    Retorna dict com: foco_sessao, tecnicas, progressao, observacoes.
    """
    ultima_sessao = ""
    for s in sessoes_recentes[:3]:
        nota = s.get("nota") or s.get("conduta") or s.get("queixa") or ""
        if nota:
            ultima_sessao += f"Sessão de {s.get('data', '')}:\n{nota}\n\n---\n\n"
    if not ultima_sessao:
        ultima_sessao = "Nenhuma sessão anterior registrada."

    conduta_bloco = f"CONDUTA DE TRATAMENTO ESTABELECIDA:\n{conduta}\n\n" if conduta else ""

    prompt = f"""Você é um fisioterapeuta clínico experiente. O fisioterapeuta está prestes a iniciar uma sessão hoje e precisa de uma orientação prática sobre o que fazer.

Com base nas informações abaixo, sugira o que priorizar na sessão de HOJE — seja objetivo e prático.

ANAMNESE DO PACIENTE:
{anamnese}

{conduta_bloco}SESSÕES RECENTES (contexto de evolução):
{ultima_sessao}

Retorne APENAS um JSON válido com esta estrutura (cada campo é uma lista de strings curtas e práticas):
{{
  "foco_sessao": ["objetivo principal de hoje", ...],
  "tecnicas": ["técnica 1 com parâmetro se aplicável", ...],
  "progressao": ["ajuste ou progressão recomendada vs sessão anterior", ...],
  "observacoes": ["ponto de atenção específico para hoje", ...]
}}

Regras:
- Máximo 3 itens por lista — seja direto e clínico
- Use linguagem técnica da fisioterapia brasileira
- Baseie-se APENAS nas informações fornecidas
- "progressao" deve comparar com a sessão anterior quando houver dados; caso contrário, omita ou coloque []
- Responda APENAS com o JSON, sem texto adicional"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("sugestao_do_dia", message, owner_email)

    raw_text = message.content[0].text.strip()
    json_match = re.search(r"\{[\s\S]*\}", raw_text)
    if json_match:
        raw_text = json_match.group(0)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        result = {}

    for chave in ("foco_sessao", "tecnicas", "progressao", "observacoes"):
        if chave not in result or not isinstance(result[chave], list):
            result[chave] = []

    return result


async def feedback_clinico(
    anamnese: str,
    conduta: str | None,
    sessoes_recentes: list[dict],
    owner_email: str | None = None,
) -> dict:
    """
    Analisa a conduta planejada versus o que foi registrado nas evoluções diárias
    e gera um feedback sutil ao fisioterapeuta sobre itens pendentes ou negligenciados.
    Retorna dict com: pendencias, atencao, positivo.
    """
    sessoes_texto = []
    for i, s in enumerate(sessoes_recentes[:10]):
        nota = s.get("nota") or s.get("conduta") or s.get("queixa") or ""
        data = s.get("data") or s.get("criado_em") or ""
        if nota:
            sessoes_texto.append(f"Sessão {i + 1} — {data}:\n{nota}")

    historico_bloco = "\n\n---\n\n".join(sessoes_texto) if sessoes_texto else "Nenhuma sessão registrada ainda."
    conduta_bloco = conduta or "Conduta de tratamento não registrada."

    prompt = f"""Você é um supervisor clínico experiente em fisioterapia. Sua função é dar um retorno construtivo e discreto ao fisioterapeuta com base no histórico do paciente.

Analise o plano de tratamento (conduta) em comparação com as evoluções diárias registradas. Identifique:
1. Procedimentos ou objetivos da conduta que ainda não apareceram nas evoluções (podem estar pendentes)
2. Queixas ou sinais registrados nas sessões que merecem atenção e ainda não foram abordados no plano
3. Algo que esteja sendo bem executado (para equilibrar o feedback)

ANAMNESE DO PACIENTE:
{anamnese or "Não informada."}

CONDUTA DE TRATAMENTO PLANEJADA:
{conduta_bloco}

EVOLUÇÕES DIÁRIAS (últimas 10 sessões, mais recente primeiro):
{historico_bloco}

Retorne APENAS um JSON válido com esta estrutura:
{{
  "pendencias": ["item pendente ou não registrado nas sessões", ...],
  "atencao": ["observação clínica que merece revisão no plano", ...],
  "positivo": ["aspecto bem conduzido no tratamento", ...]
}}

Regras ESSENCIAIS:
- Tom: profissional, respeitoso e construtivo — nunca crítico ou acusatório. Use "ainda não registrado", "pode valer revisar", "vale considerar" ao invés de "deixou de fazer" ou "esqueceu"
- Máximo 3 itens por lista — seja específico e baseado nos dados
- Se não houver pendências claras, coloque [] em "pendencias"
- Baseie-se EXCLUSIVAMENTE nas informações fornecidas — nunca invente procedimentos
- Use linguagem clínica técnica da fisioterapia brasileira
- Responda APENAS com o JSON, sem texto adicional"""

    message = await client.messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("feedback_clinico", message, owner_email)

    raw_text = message.content[0].text.strip()
    json_match = re.search(r"\{[\s\S]*\}", raw_text)
    if json_match:
        raw_text = json_match.group(0)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        result = {}

    for chave in ("pendencias", "atencao", "positivo"):
        if chave not in result or not isinstance(result[chave], list):
            result[chave] = []

    return result


async def interpretar_agendamento(texto: str, data_hoje: str, owner_email: str | None = None) -> dict:
    """
    Interpreta um pedido de agendamento em linguagem natural.
    Retorna: {nome, data (YYYY-MM-DD), hora_inicio (HH:MM), hora_fim (HH:MM)}
    """
    prompt = f"""Hoje é {data_hoje}.

Pedido do usuário: "{texto}"

Extraia as informações de agendamento. Responda APENAS com JSON válido, sem markdown, sem explicações:
{{
  "nome": "nome da pessoa ou título do evento",
  "data": "YYYY-MM-DD",
  "hora_inicio": "HH:MM",
  "hora_fim": "HH:MM"
}}

Regras:
- Interprete expressões relativas como "amanhã", "depois de amanhã", "sexta-feira", "semana que vem", "daqui 3 dias"
- Se o usuário informou só hora de início e nenhuma duração, assuma 1 hora
- Se não informou horário, use "09:00" como padrão
- Horário em formato 24h (ex: "14:30", "08:00")
- Se o usuário disse "das X às Y" use exatamente esses horários
- Responda APENAS o JSON"""
    message = await client.messages.create(
        model=MODEL,
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("interpretar_agendamento", message, owner_email)
    raw = message.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


async def interpretar_atestado(texto: str, data_hoje: str, paciente_nome: str, owner_email: str | None = None) -> dict:
    """
    Interpreta pedido de atestado em linguagem natural.
    Retorna: {data (YYYY-MM-DD), hora_inicio (HH:MM), hora_fim (HH:MM), motivo, conduta}
    """
    prompt = f"""Você é um fisioterapeuta clínico experiente com domínio completo de terminologia clínica, anatomia, biomecânica e reabilitação. Entende perfeitamente termos técnicos do universo da fisioterapia brasileira (TENS, FES, ultrassom terapêutico, laser, RPG, Pilates clínico, Cinesioterapia, PNF, McKenzie, Maitland, bandagem funcional, mobilização neural, drenagem linfática, entre outros).

Hoje é {data_hoje}. Paciente: {paciente_nome}.

O fisioterapeuta disse: "{texto}"

Extraia as informações para um atestado de fisioterapia. Responda APENAS com JSON válido, sem markdown, sem explicações:
{{
  "data": "YYYY-MM-DD",
  "hora_inicio": "HH:MM",
  "hora_fim": "HH:MM",
  "motivo": "motivo do atendimento em linguagem clínica formal",
  "conduta": "conduta fisioterapêutica aplicada em linguagem clínica formal"
}}

Regras:
- "data" é a data do atendimento — use hoje se não informada
- Se não informou horários, deixe hora_inicio e hora_fim como strings vazias ""
- Interprete expressões como "das 8 às 9", "das 14h30 às 15h30", "amanhã" etc.
- Converta linguagem coloquial em termos clínicos formais:
  - Ex motivo: "dor nas costas" → "lombalgia crônica", "dor no joelho" → "gonalgia", "cirurgia de ombro" → "reabilitação pós-operatória de ombro"
  - Ex conduta: "fiz laser e exercício" → "aplicação de laserterapia de baixa potência e cinesioterapia ativa"
  - Preserve termos técnicos já corretos (TENS, RPG, Pilates clínico, etc.)
- Linguagem formal, em terceira pessoa implícita, adequada para documento oficial
- Responda APENAS o JSON"""
    message = await client.messages.create(
        model=MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    _registrar("interpretar_atestado", message, owner_email)
    raw = message.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


async def extrair_valor_sessao(transcricao: str, owner_email: str | None = None) -> float | None:
    """Tenta extrair valor monetário de sessão avulsa da transcrição. Retorna float ou None."""
    if not transcricao or not transcricao.strip():
        return None
    prompt = (
        "Da transcrição abaixo, o fisioterapeuta mencionou explicitamente um valor em reais "
        "para cobrar pela sessão (ex: 'cobrei 280', 'valor de 300 reais', 'sessão de R$ 250')?\n"
        "Responda APENAS com o número decimal (ex: 280.00) ou com a palavra null.\n\n"
        f"Transcrição:\n{transcricao[:2000]}"
    )
    try:
        message = await client.messages.create(
            model=MODEL,
            max_tokens=16,
            messages=[{"role": "user", "content": prompt}],
        )
        _registrar("extrair_valor_sessao", message, owner_email)
        texto = message.content[0].text.strip().lower()
        if texto == "null" or not texto:
            return None
        match = re.search(r"[\d]+(?:[.,][\d]+)?", texto)
        if match:
            return float(match.group(0).replace(",", "."))
    except Exception:
        pass
    return None


import datetime
import json
import logging
import os
from enum import Enum

from fastapi import APIRouter, Request, Form, HTTPException, Header, Response
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

import ai
import database as db

router = APIRouter()
logger = logging.getLogger("physio_notes.bot")


class Passo(str, Enum):
    MENU = "MENU"
    AGUARDANDO_HORARIO = "AGUARDANDO_HORARIO"
    PEDINDO_DADOS = "PEDINDO_DADOS"
    CONFIRMANDO = "CONFIRMANDO"
    CORRIGINDO_DADOS = "CORRIGINDO_DADOS"


CMDS_SAIR = {"0", "sair", "tchau", "encerrar"}
CMDS_MENU = {"menu", "inicio", "início", "voltar ao menu"}
CMDS_VOLTAR = {"voltar", "back", "00"}

PASSO_ANTERIOR = {
    Passo.AGUARDANDO_HORARIO.value: None,
    Passo.PEDINDO_DADOS.value: Passo.AGUARDANDO_HORARIO.value,
    Passo.CONFIRMANDO.value: Passo.PEDINDO_DADOS.value,
    Passo.CORRIGINDO_DADOS.value: Passo.CONFIRMANDO.value,
}

NAV_LIMIT = 3
RETRY_LIMIT = 4
BLACKLIST_HITS_LIMIT = 5

MSG_BLACKLISTED = (
    "👤 *Atendimento Humano*\n\n"
    "Seu contato foi encaminhado exclusivamente para um de nossos atendentes.\n"
    "Em breve alguém entrará em contato com você. 🙏"
)

MSG_LOOP_HUMANO = (
    "😊 Percebi que você está tendo dificuldades para finalizar o atendimento.\n\n"
    "👤 Vou encaminhar para um de nossos atendentes que poderá te ajudar melhor!\n\n"
    "⏳ Aguarde um momento, alguém entrará em contato em breve."
)

MSG_MENU_OPCOES = (
    "Como posso te ajudar hoje?\n\n"
    "1️⃣  Marcar consulta\n"
    "2️⃣  Reagendar consulta\n"
    "3️⃣  Cancelar consulta\n"
    "4️⃣  Sair da conversa"
)

DICA_NAV = "\n\nComandos: *00 voltar* • *menu início* • *0 sair*"

MENU_AGENDAR = {"1", "marcar consulta", "agendar", "agendamento", "quero marcar", "quero agendar"}
MENU_REAGENDAR = {"2", "reagendar", "remarcar", "alterar consulta"}
MENU_CANCELAR = {"3", "cancelar", "cancelar consulta"}
MENU_SAIR = {"4", "sair da conversa"}

CONFIRMAR_OPCOES = {"1", "sim", "confirmar", "confirmo", "pode confirmar", "agendar", "fechar"}
CORRIGIR_DADOS_OPCOES = {
    "2",
    "dados incorretos",
    "corrigir dados",
    "alterar dados",
    "corrigir nome e email",
    "corrigir nome e e-mail",
    "nome e email",
    "nome e e-mail",
}
CORRIGIR_HORARIO_OPCOES = {
    "3",
    "corrigir horario",
    "corrigir horário",
    "alterar horario",
    "alterar horário",
    "horario",
    "horário",
}


def _texto_normalizado(texto: str) -> str:
    return " ".join(texto.lower().strip().split())


def _get_session_data(session: dict | None) -> dict:
    if session and session.get("dados_json"):
        try:
            return json.loads(session["dados_json"])
        except json.JSONDecodeError:
            logger.warning("dados_json inválido para sessão WhatsApp; usando dict vazio")
    return {}


def _save_session(telefone: str, passo: str, dados: dict | None = None) -> None:
    if dados is None:
        db.update_whatsapp_session(telefone, passo)
    else:
        db.update_whatsapp_session(telefone, passo, json.dumps(dados, ensure_ascii=False))


def _retry(dados: dict, chave: str) -> tuple[dict, bool]:
    dados[chave] = dados.get(chave, 0) + 1
    return dados, dados[chave] >= RETRY_LIMIT


def _reset_retry_counters(dados: dict) -> dict:
    for chave in ("retry_menu", "retry_horario", "retry_dados", "retry_confirmando"):
        dados.pop(chave, None)
    return dados


def _reset_flow_flags(dados: dict) -> dict:
    dados["nav_count"] = 0
    dados.pop("strike_ia", None)
    return dados


def _menu_principal() -> str:
    return "🏠 *Menu principal*\n\n" + MSG_MENU_OPCOES + DICA_NAV


def _mensagem_horario(horario_atual: str | None = None) -> str:
    complemento = f"\n\nHorário atual salvo: *{horario_atual}*" if horario_atual else ""
    return (
        "📍 *Etapa 1/3 — Horário da consulta*\n\n"
        "Perfeito! Vamos agendar sua consulta. 📅\n\n"
        "Me informe o melhor *dia e horário* para você.\n\n"
        "Exemplos:\n"
        "• segunda às 14h\n"
        "• 28/07 às 10:30"
        f"{complemento}"
        + DICA_NAV
    )


def _mensagem_dados(horario: str | None) -> str:
    horario_txt = horario or "não informado"
    return (
        "📍 *Etapa 2/3 — Seus dados*\n\n"
        f"Já anotei este horário: *{horario_txt}*\n\n"
        "Agora envie:\n"
        "👤 *Nome completo*\n"
        "📧 *E-mail*\n\n"
        "Exemplo:\n"
        "João Silva - joao@email.com"
        + DICA_NAV
    )


def _mensagem_confirmacao(dados: dict) -> str:
    return (
        "📍 *Etapa 3/3 — Confirmar agendamento*\n\n"
        "Confira os dados antes de finalizar:\n\n"
        f"🗓️ *Horário:* {dados.get('horario_desejado', 'N/D')}\n"
        f"👤 *Nome:* {dados.get('nome', 'Não informado')}\n"
        f"📧 *E-mail:* {dados.get('email', 'Não informado')}\n\n"
        "Os dados estão corretos?\n\n"
        "1️⃣  Sim, confirmar agendamento\n"
        "2️⃣  Não, corrigir nome e e-mail\n"
        "3️⃣  Corrigir horário"
        + DICA_NAV
    )



def _deve_moderar(texto: str, texto_norm: str, passo: str | None) -> bool:
    if len(texto.strip()) <= 2:
        return False
    if texto_norm in CMDS_SAIR or texto_norm in CMDS_MENU or texto_norm in CMDS_VOLTAR:
        return False
    if passo in {
        Passo.AGUARDANDO_HORARIO.value,
        Passo.PEDINDO_DADOS.value,
        Passo.CORRIGINDO_DADOS.value,
        Passo.CONFIRMANDO.value,
    }:
        return False
    return True


def enviar_mensagem_proativa(telefone: str, msg: str) -> bool:
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "")
    if not account_sid or not auth_token or not from_number:
        logger.warning("Variáveis Twilio não configuradas — envio proativo desativado")
        return False
    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        client.messages.create(
            from_=f"whatsapp:{from_number}",
            to=telefone,
            body=msg,
        )
        return True
    except Exception as e:
        logger.error(f"Falha ao enviar mensagem proativa para {telefone}: {e}")
        return False


def build_response(msg: str):
    resp = MessagingResponse()
    resp.message(msg)
    return Response(content=str(resp), media_type="application/xml")


async def _handle_dados_input(telefone: str, texto: str, passo: str, dados: dict):
    is_corrigindo = passo == Passo.CORRIGINDO_DADOS.value
    log_label = "corrigir" if is_corrigindo else "extrair"

    try:
        analise = await ai.extrair_nome_email_bot(texto)
    except Exception as e:
        logger.error(f"Falha ao {log_label} nome/email via IA: {e}")
        analise = {"valido": False, "nome_encontrado": None, "email_encontrado": None}

    nome = analise.get("nome_encontrado")
    email = analise.get("email_encontrado")

    if not analise.get("valido") or not nome or not email:
        dados, esgotado = _retry(dados, "retry_dados")
        if esgotado:
            blacklisted = db.increment_shield_hit(telefone, "retry_dados", BLACKLIST_HITS_LIMIT)
            db.end_whatsapp_session(telefone)
            return build_response(MSG_BLACKLISTED if blacklisted else MSG_LOOP_HUMANO)

        _save_session(telefone, passo, dados)
        msg_base = (
            "😕 Não consegui identificar corretamente seu nome e e-mail.\n\n"
            "Por favor, envie assim:\n\n"
            "👤 *Nome:* João Silva\n"
            "📧 *E-mail:* joao@email.com\n\n"
            "Ou tudo em uma linha:\n"
            "João Silva - joao@email.com"
            if not is_corrigindo
            else "😕 Não consegui identificar corretamente o nome e o e-mail.\n\n"
            "Por favor, envie novamente assim:\n\n"
            "João Silva - joao@email.com"
        )
        return build_response(msg_base + DICA_NAV)

    dados = _reset_retry_counters(dados)
    dados = _reset_flow_flags(dados)
    dados["nome"] = nome
    dados["email"] = email
    if not is_corrigindo:
        dados.pop("retornar_para_confirmacao_apos_horario", None)
    _save_session(telefone, Passo.CONFIRMANDO.value, dados)
    return build_response(_mensagem_confirmacao(dados))


@router.post("/api/twilio/webhook")
async def twilio_webhook(
    request: Request,
    From: str = Form(...),
    Body: str = Form(...),
    x_twilio_signature: str = Header(None),
):
    url = str(request.url)

    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not auth_token:
        logger.warning("TWILIO_AUTH_TOKEN não configurado — validação de assinatura desativada")
    else:
        validator = RequestValidator(auth_token)
        form_data = await request.form()
        post_vars = dict(form_data)

        header_proto = request.headers.get("x-forwarded-proto")
        if header_proto == "https":
            url = url.replace("http://", "https://")

        if not validator.validate(url, post_vars, x_twilio_signature or ""):
            logger.warning(f"Tentativa de acesso não autorizada ao webhook de: {url}")
            raise HTTPException(status_code=403, detail="Assinatura Twilio inválida. Acesso bloqueado.")

    texto = Body.strip()
    texto_norm = _texto_normalizado(texto)
    telefone = From

    if db.is_whatsapp_blacklisted(telefone):
        logger.info(f"Mensagem bloqueada (blacklist): {telefone}")
        db.end_whatsapp_session(telefone)
        return build_response(MSG_BLACKLISTED)

    session = db.get_whatsapp_session(telefone)
    passo = session["passo_atual"] if session else None
    dados = _get_session_data(session)

    if texto_norm in CMDS_SAIR:
        db.end_whatsapp_session(telefone)
        return build_response(
            "👋 Tudo bem! Foi um prazer atender você.\n\n"
            "Se precisar de algo no futuro, é só nos chamar. Tenha um ótimo dia! ☀️"
        )

    if texto_norm in CMDS_MENU or (texto_norm in CMDS_VOLTAR and passo):
        dados["nav_count"] = dados.get("nav_count", 0) + 1

        if dados["nav_count"] > NAV_LIMIT:
            logger.info(f"Loop de navegação detectado para {telefone}: {dados['nav_count']} navegações")
            blacklisted = db.increment_shield_hit(telefone, "loop_navegacao", BLACKLIST_HITS_LIMIT)
            db.end_whatsapp_session(telefone)
            return build_response(MSG_BLACKLISTED if blacklisted else MSG_LOOP_HUMANO)

        if texto_norm in CMDS_MENU:
            dados = _reset_retry_counters(dados)
            _save_session(telefone, Passo.MENU.value, dados)
            return build_response(_menu_principal())

        anterior = PASSO_ANTERIOR.get(passo)
        if anterior is None:
            dados = _reset_retry_counters(dados)
            _save_session(telefone, Passo.MENU.value, dados)
            return build_response(_menu_principal())

        if anterior == Passo.AGUARDANDO_HORARIO.value:
            _save_session(telefone, anterior, dados)
            return build_response(_mensagem_horario(dados.get("horario_desejado")))

        if anterior == Passo.PEDINDO_DADOS.value:
            _save_session(telefone, anterior, dados)
            return build_response(_mensagem_dados(dados.get("horario_desejado")))

        _save_session(telefone, anterior, dados)
        return build_response(_mensagem_confirmacao(dados))

    if _deve_moderar(texto, texto_norm, passo):
        try:
            moderacao = await ai.verificar_intencao_usuario_bot(texto)
        except Exception as e:
            logger.error(f"Falha na moderação IA: {e}")
            moderacao = {"bloquear": False}

        if moderacao.get("bloquear"):
            strikes = dados.get("strike_ia", 0)
            if strikes == 0:
                dados["strike_ia"] = 1
                _save_session(telefone, passo or Passo.MENU.value, dados)
                db.increment_shield_hit(telefone, "anti_troll_strike1", BLACKLIST_HITS_LIMIT)
                return build_response(
                    "⚠️ Não consegui compreender essa mensagem no contexto do atendimento.\n\n"
                    "Por favor, responda com uma opção válida do fluxo:\n"
                    "• número da opção\n"
                    "• data e horário\n"
                    "• nome e e-mail\n\n"
                    "💡 Você também pode digitar *menu* para recomeçar."
                )

            blacklisted = db.increment_shield_hit(telefone, "anti_troll_strike2", BLACKLIST_HITS_LIMIT)
            db.end_whatsapp_session(telefone)
            return build_response(
                MSG_BLACKLISTED
                if blacklisted
                else "😔 Não foi possível continuar o atendimento automático desta vez.\n\n"
                "Um de nossos atendentes poderá te ajudar melhor. Agradecemos a compreensão. 🙏"
            )

    if not session:
        _save_session(telefone, Passo.MENU.value, {"nav_count": 0})
        return build_response(
            "Olá! 👋 Bem-vindo ao atendimento da nossa clínica.\n\n" + _menu_principal()
        )

    if passo == Passo.MENU.value:
        if texto_norm in MENU_AGENDAR:
            dados = _reset_retry_counters(dados)
            dados = _reset_flow_flags(dados)
            dados.pop("retornar_para_confirmacao_apos_horario", None)
            _save_session(telefone, Passo.AGUARDANDO_HORARIO.value, dados)
            return build_response(_mensagem_horario())

        if texto_norm in MENU_REAGENDAR or texto_norm in MENU_CANCELAR:
            db.end_whatsapp_session(telefone)
            return build_response(
                "📋 Entendido!\n\n"
                "👤 Em breve um de nossos atendentes entrará em contato com você para realizar essa alteração.\n\n"
                "⏳ Agradecemos a paciência!"
            )

        if texto_norm in MENU_SAIR:
            db.end_whatsapp_session(telefone)
            return build_response(
                "👋 Tudo bem! Foi um prazer atender você.\n\n"
                "Se precisar de algo no futuro, é só nos chamar. Tenha um ótimo dia! ☀️"
            )

        dados, esgotado = _retry(dados, "retry_menu")
        if esgotado:
            blacklisted = db.increment_shield_hit(telefone, "retry_menu", BLACKLIST_HITS_LIMIT)
            db.end_whatsapp_session(telefone)
            return build_response(MSG_BLACKLISTED if blacklisted else MSG_LOOP_HUMANO)

        _save_session(telefone, Passo.MENU.value, dados)
        return build_response("😅 Não reconheci essa opção.\n\n" + _menu_principal())

    if passo == Passo.AGUARDANDO_HORARIO.value:
        data_hoje = datetime.date.today().isoformat()
        try:
            analise_horario = await ai.extrair_horario_bot(texto, data_hoje)
        except Exception as e:
            logger.error(f"Falha ao extrair horário via IA: {e}")
            analise_horario = {"valido": False, "horario_normalizado": None}

        if not analise_horario.get("valido"):
            dados, esgotado = _retry(dados, "retry_horario")
            if esgotado:
                blacklisted = db.increment_shield_hit(telefone, "retry_horario", BLACKLIST_HITS_LIMIT)
                db.end_whatsapp_session(telefone)
                return build_response(MSG_BLACKLISTED if blacklisted else MSG_LOOP_HUMANO)

            _save_session(telefone, Passo.AGUARDANDO_HORARIO.value, dados)
            return build_response(
                "😕 Não consegui identificar um horário válido.\n\n"
                "Envie no formato, por exemplo:\n"
                "• segunda às 14h\n"
                "• amanhã de manhã\n"
                "• 28/07 às 10:30"
                + DICA_NAV
            )

        horario = analise_horario.get("horario_normalizado") or texto
        dados = _reset_retry_counters(dados)
        dados = _reset_flow_flags(dados)
        dados["horario_desejado"] = horario

        if dados.get("retornar_para_confirmacao_apos_horario"):
            dados.pop("retornar_para_confirmacao_apos_horario", None)
            _save_session(telefone, Passo.CONFIRMANDO.value, dados)
            return build_response(_mensagem_confirmacao(dados))

        _save_session(telefone, Passo.PEDINDO_DADOS.value, dados)
        return build_response(_mensagem_dados(horario))

    if passo in {Passo.PEDINDO_DADOS.value, Passo.CORRIGINDO_DADOS.value}:
        return await _handle_dados_input(telefone, texto, passo, dados)

    if passo == Passo.CONFIRMANDO.value:
        if texto_norm in CONFIRMAR_OPCOES:
            horario_desejado = dados.get("horario_desejado", "N/D")
            nome_db = dados.get("nome", "Não informado")
            email_db = dados.get("email", "Não informado")
            try:
                db.criar_agendamento(nome_db, email_db, telefone, horario_desejado)
                db.end_whatsapp_session(telefone)
                return build_response(
                    "🎉 *Consulta agendada com sucesso!*\n\n"
                    f"🗓️ *Horário:* {horario_desejado}\n"
                    f"👤 *Nome:* {nome_db}\n"
                    f"📧 *E-mail:* {email_db}\n\n"
                    "✅ Seu horário está reservado.\n"
                    "📋 Guarde esta conversa como comprovante.\n\n"
                    "Agradecemos a preferência! Até breve. 😊"
                )
            except Exception as e:
                logger.error(f"Erro agendamento bot: {e}")
                _save_session(telefone, Passo.CONFIRMANDO.value, dados)
                return build_response(
                    "⚠️ Ops! Algo deu errado ao finalizar o agendamento.\n\n"
                    "Por favor, tente confirmar novamente ou entre em contato diretamente com a clínica."
                )

        if texto_norm in CORRIGIR_DADOS_OPCOES:
            dados = _reset_retry_counters(dados)
            _save_session(telefone, Passo.CORRIGINDO_DADOS.value, dados)
            return build_response(
                "✏️ Sem problemas.\n\n"
                "Por favor, envie novamente seu *nome completo* e *e-mail*.\n\n"
                "Exemplo:\n"
                "João Silva - joao@email.com"
                + DICA_NAV
            )

        if texto_norm in CORRIGIR_HORARIO_OPCOES:
            dados = _reset_retry_counters(dados)
            dados["retornar_para_confirmacao_apos_horario"] = True
            _save_session(telefone, Passo.AGUARDANDO_HORARIO.value, dados)
            return build_response(_mensagem_horario(dados.get("horario_desejado")))

        dados, esgotado = _retry(dados, "retry_confirmando")
        if esgotado:
            blacklisted = db.increment_shield_hit(telefone, "retry_confirmando", BLACKLIST_HITS_LIMIT)
            db.end_whatsapp_session(telefone)
            return build_response(MSG_BLACKLISTED if blacklisted else MSG_LOOP_HUMANO)

        _save_session(telefone, Passo.CONFIRMANDO.value, dados)
        return build_response(
            "😅 Não entendi. Responda com uma das opções abaixo:\n\n"
            "1️⃣  Sim, confirmar agendamento\n"
            "2️⃣  Não, corrigir nome e e-mail\n"
            "3️⃣  Corrigir horário\n\n"
            "Você também pode responder com texto, como:\n"
            "• confirmar\n"
            "• corrigir dados\n"
            "• corrigir horário"
            + DICA_NAV
        )

    db.end_whatsapp_session(telefone)
    return build_response(
        "⏰ Sua sessão expirou por inatividade.\n\n"
        "Diga *Oi* para iniciar um novo atendimento! 🤖"
    )
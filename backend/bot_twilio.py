import json
import logging
import os
from fastapi import APIRouter, Request, Form, HTTPException, Header
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator

import database as db

router = APIRouter()
logger = logging.getLogger("physio_notes.bot")

def build_response(msg: str) -> str:
    from fastapi import Response
    resp = MessagingResponse()
    resp.message(msg)
    # Twilio Webhooks precisam ser do tipo application/xml
    return Response(content=str(resp), media_type="application/xml")

@router.post("/api/twilio/webhook")
async def twilio_webhook(
    request: Request, 
    From: str = Form(...), 
    Body: str = Form(...),
    x_twilio_signature: str = Header(None)
):
    url = str(request.url)
    
    # Validação de segurança Twilio
    is_localhost = "localhost" in url or "127.0.0.1" in url
    if not is_localhost:
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        if auth_token:
            validator = RequestValidator(auth_token)
            form_data = await request.form()
            post_vars = dict(form_data)
            
            # Repara URL pública original em proxies (Twilio checa assinatura https estritamente)
            header_proto = request.headers.get("x-forwarded-proto")
            if header_proto and header_proto == "https":
                url = url.replace("http://", "https://")
                
            if not validator.validate(url, post_vars, x_twilio_signature or ""):
                logger.warning(f"Tentativa de acesso não autorizada ao webhook de: {url}")
                raise HTTPException(status_code=403, detail="Assinatura Twilio Inválida. Acesso Bloqueado.")

    texto = Body.strip()
    telefone = From
    
    session = db.get_whatsapp_session(telefone)
    
    # --- Escudo Anti-Troll IA ---
    if len(texto) > 2:
        import ai
        moderacao = await ai.verificar_intencao_usuario_bot(texto)
        if moderacao.get("bloquear"):
            dados = json.loads(session["dados_json"]) if (session and session.get("dados_json")) else {}
            strikes = dados.get("strike_ia", 0)
            
            if strikes == 0:
                dados["strike_ia"] = 1
                if not session:
                    db.update_whatsapp_session(telefone, "MENU", json.dumps(dados))
                else:
                    db.update_whatsapp_session(telefone, session["passo_atual"], json.dumps(dados))
                
                return build_response("Desculpe, não consegui compreender sua mensagem neste contexto ou o assunto foge do escopo do nosso atendimento clínico. Por favor, tente responder com a informação que estamos solicitando (escolhas numéricas, data/horário ou seus dados), ou digite '4' para cancelar o atendimento.")
            else:
                db.end_whatsapp_session(telefone)
                return build_response("Identificamos repetidas mensagens que fogem ao escopo do atendimento ou desrespeitam as diretrizes. O suporte automatizado para este contato foi encerrado de forma definitiva. Agradecemos a compreensão.")
    # ----------------------------
    
    if not session:
        # Passo 1: Saudação
        msg = (
            "Olá! Obrigado por entrar em contato conosco. 🤖\n\n"
            "Como posso te ajudar hoje? Por favor, digite o número da opção desejada:\n\n"
            "1️⃣ - Marcar consulta\n"
            "2️⃣ - Reagendar\n"
            "3️⃣ - Cancelar\n"
            "4️⃣ - Sair da conversa"
        )
        db.update_whatsapp_session(telefone, "MENU")
        return build_response(msg)
    
    passo = session["passo_atual"]
    
    if passo == "MENU":
        if texto == "1":
            msg = "Perfeito! Para agendarmos sua consulta, por favor, me informe qual o melhor **dia e horário** para você."
            db.update_whatsapp_session(telefone, "AGUARDANDO_HORARIO")
            return build_response(msg)
        elif texto in ["2", "3"]:
            msg = "Entendido! Um de nossos atendentes humanos já foi notificado e dará continuidade ao seu atendimento em instantes para realizar essa alteração. Aguarde um momento! 👤"
            db.end_whatsapp_session(telefone)
            return build_response(msg)
        elif texto == "4":
            msg = "Tudo bem! Se precisar de algo no futuro, estaremos por aqui. Tenha um ótimo dia! 👋"
            db.end_whatsapp_session(telefone)
            return build_response(msg)
        else:
            return build_response("Desculpe, não entendi. Por favor, digite apenas um dígito válido (1, 2, 3 ou 4).")
            
    elif passo == "AGUARDANDO_HORARIO":
        dados = {"horario_desejado": texto}
        db.update_whatsapp_session(telefone, "PEDINDO_DADOS", json.dumps(dados))
        
        msg = (
            "Maravilha! Temos esse horário livre sim. 📅\n\n"
            "Para finalizarmos a sua reserva, por favor, me informe o seu **Nome Completo** e o seu **E-mail**."
        )
        return build_response(msg)

    elif passo == "PEDINDO_DADOS":
        import ai
        analise = await ai.extrair_nome_email_bot(texto)
        if not analise.get("valido") or (not analise.get("nome_encontrado") and not analise.get("email_encontrado")):
            return build_response("Desculpe, mas não consegui identificar seu nome ou e-mail nesta mensagem. Por favor, escreva de forma clara (ex: João Silva - joao@email.com).")
            
        nome = analise.get("nome_encontrado") or "Não informado"
        email = analise.get("email_encontrado") or "Não informado"
        string_final = f"{nome} / {email}"

        dados = json.loads(session["dados_json"]) if session.get("dados_json") else {}
        dados["nome_email"] = string_final
        db.update_whatsapp_session(telefone, "CONFIRMANDO", json.dumps(dados))
        
        msg = (
            "Quase lá! Para não termos nenhum erro, por favor, confira os dados fornecidos:\n\n"
            f"👤/📧 **Dados informados:** {string_final}\n\n"
            "Está tudo certo?\n"
            "1️⃣ - Sim, pode confirmar!\n"
            "2️⃣ - Não, quero corrigir os dados."
        )
        return build_response(msg)
        
    elif passo == "CONFIRMANDO":
        dados = json.loads(session["dados_json"]) if session.get("dados_json") else {}
        if texto == "1":
            try:
                horario_desejado = dados.get("horario_desejado", "N/D")
                nome_email = dados.get("nome_email", "N/D")
                db.criar_agendamento(nome_email, "Não coletado formatado", telefone, horario_desejado)
                
                msg = (
                    "Tudo certo! Sua consulta foi agendada com sucesso. ✅📋\n"
                    "Agradecemos muito a preferência e nos vemos em breve!"
                )
            except Exception as e:
                logger.error(f"Erro agendamento bot: {e}")
                msg = "Ocorreu um erro interno. Por favor, tente novamente mais tarde."
            db.end_whatsapp_session(telefone)
            return build_response(msg)
        elif texto == "2":
            # Volta para pegar os dados corretos
            db.update_whatsapp_session(telefone, "PEDINDO_DADOS", json.dumps({"horario_desejado": dados.get("horario_desejado")}))
            msg = "Sem problemas! Vamos tentar de novo.\nPor favor, digite novamente o seu **Nome Completo** e o seu **E-mail**."
            return build_response(msg)
        else:
            return build_response("Por favor, responda com 1 para confirmar ou 2 para corrigir os dados.")
            
    else:
        db.end_whatsapp_session(telefone)
        return build_response("Sessão expirada. Diga um 'Oi' para recomeçar o atendimento! 🤖")

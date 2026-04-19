"""
Web Push Notifications — Physio Notes
Envio de push e jobs agendados (APScheduler).
"""
import os
import json
import logging
from datetime import date, timedelta

import database as db

logger = logging.getLogger("notifications")

VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY  = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_EMAIL       = os.getenv("VAPID_EMAIL", "mailto:admin@physionotes.app")

_push_enabled = bool(VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY)

# ── Envio ─────────────────────────────────────────────────────────────────────

def enviar_push(subscription_json: str, title: str, body: str, url: str = "/") -> bool:
    """Envia push para uma subscription. Retorna True se enviou com sucesso."""
    if not _push_enabled:
        return False
    try:
        from pywebpush import webpush, WebPushException
        sub = json.loads(subscription_json)
        webpush(
            subscription_info=sub,
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_EMAIL},
        )
        return True
    except Exception as e:
        # Se 404/410: subscription expirada — remove do banco
        resp = getattr(getattr(e, "response", None), "status_code", None)
        if resp in (404, 410):
            try:
                sub = json.loads(subscription_json)
                db.remover_subscription_por_endpoint(sub.get("endpoint", ""))
            except Exception:
                pass
        logger.warning(f"Push falhou: {e}")
        return False


def notificar_owner(owner_email: str, title: str, body: str, url: str = "/"):
    """Envia push para todos os devices registrados de um usuário."""
    for sub in db.get_subscriptions_por_owner(owner_email):
        enviar_push(sub["subscription_json"], title, body, url)


# ── Jobs agendados ─────────────────────────────────────────────────────────────

def job_sessoes_abertas():
    """20h: avisa fisios com sessão aberta não encerrada."""
    for owner_email, nomes in db.get_sessoes_abertas_por_owner().items():
        qtd = len(nomes)
        lista = ", ".join(nomes[:3]) + ("..." if qtd > 3 else "")
        notificar_owner(
            owner_email,
            "Sessão aberta sem fechar ⚠️",
            f"{qtd} sessão(ões) ainda aberta(s) hoje: {lista}",
        )


def job_aniversariantes():
    """8h: avisa sobre aniversários do dia."""
    for owner_email, nomes in db.get_aniversariantes_hoje_por_owner().items():
        lista = ", ".join(nomes)
        notificar_owner(
            owner_email,
            "🎂 Aniversário de paciente hoje",
            f"{lista} faz(em) aniversário hoje!",
        )


def job_pacientes_sem_sessao():
    """Segunda 9h: pacientes sem sessão há 30+ dias."""
    for owner_email, nomes in db.get_pacientes_sem_sessao_recente_por_owner(dias=30).items():
        qtd = len(nomes)
        lista = ", ".join(nomes[:3]) + ("..." if qtd > 3 else "")
        notificar_owner(
            owner_email,
            f"{qtd} paciente(s) sem sessão há 30+ dias",
            f"{lista} não têm atendimento registrado há mais de um mês.",
        )


def job_resumo_semanal():
    """Segunda 8h: resumo da semana anterior."""
    for owner_email, resumo in db.get_resumo_semana_por_owner().items():
        sessoes = resumo["sessoes"]
        pacientes = resumo["pacientes"]
        notificar_owner(
            owner_email,
            "📊 Resumo da semana",
            f"Semana passada: {sessoes} sessão(ões) com {pacientes} paciente(s).",
        )


def job_pacotes_vencidos():
    """9h: pacotes esgotados há 7+ dias sem renovação."""
    for owner_email, nomes in db.get_pacotes_vencidos_sem_renovar_por_owner(dias=7).items():
        lista = ", ".join(nomes[:3]) + ("..." if len(nomes) > 3 else "")
        notificar_owner(
            owner_email,
            "📦 Pacote(s) esgotado(s) sem renovação",
            f"{lista} — pacote acabou há mais de 7 dias.",
        )


def notificar_pacote_quase_acabando(owner_email: str, paciente_nome: str, restantes: int):
    """Chamado imediatamente ao encerrar sessão quando restam ≤ 2."""
    notificar_owner(
        owner_email,
        f"📦 Pacote quase no fim — {paciente_nome}",
        f"Restam apenas {restantes} sessão(ões) no pacote.",
    )


# ── Scheduler ─────────────────────────────────────────────────────────────────

_scheduler = None


INATIVIDADE_AVISO_MIN = 10
INATIVIDADE_ENCERRAR_MIN = 5

MSG_AVISO_INATIVIDADE = (
    "😊 Ainda está por aí?\n\n"
    "Sua conversa está guardada — é só responder para continuar de onde parou.\n\n"
    "Caso não queira continuar, diga *0* para encerrar."
)

MSG_ENCERRAMENTO_INATIVIDADE = (
    "⏰ Encerramos sua sessão por inatividade.\n\n"
    "Quando quiser retomar o agendamento, é só nos chamar novamente! 😊"
)


def job_inatividade_bot():
    try:
        import database as db
        from bot_twilio import enviar_mensagem_proativa

        for session in db.get_sessions_para_aviso(INATIVIDADE_AVISO_MIN):
            telefone = session["telefone"]
            if enviar_mensagem_proativa(telefone, MSG_AVISO_INATIVIDADE):
                db.marcar_aviso_inatividade(telefone)
                logger.info(f"Aviso de inatividade enviado: {telefone}")

        for session in db.get_sessions_para_encerrar(INATIVIDADE_ENCERRAR_MIN):
            telefone = session["telefone"]
            enviar_mensagem_proativa(telefone, MSG_ENCERRAMENTO_INATIVIDADE)
            db.end_whatsapp_session(telefone)
            logger.info(f"Sessão encerrada por inatividade: {telefone}")
    except Exception as e:
        logger.error(f"Erro no job de inatividade do bot: {e}")


def start_scheduler():
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        _scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
        from apscheduler.triggers.interval import IntervalTrigger
        # Sessões abertas — todo dia às 20h
        _scheduler.add_job(job_sessoes_abertas,   CronTrigger(hour=20, minute=0))
        # Aniversariantes — todo dia às 8h
        _scheduler.add_job(job_aniversariantes,   CronTrigger(hour=8,  minute=0))
        # Pacotes vencidos — todo dia às 9h
        _scheduler.add_job(job_pacotes_vencidos,  CronTrigger(hour=9,  minute=0))
        # Pacientes sem sessão + resumo — toda segunda-feira
        _scheduler.add_job(job_pacientes_sem_sessao, CronTrigger(day_of_week="mon", hour=9, minute=10))
        _scheduler.add_job(job_resumo_semanal,       CronTrigger(day_of_week="mon", hour=8, minute=0))
        # Inatividade do bot WhatsApp — a cada 2 minutos
        _scheduler.add_job(job_inatividade_bot, IntervalTrigger(minutes=2))
        _scheduler.start()
        logger.info("Scheduler de notificações iniciado.")
    except Exception as e:
        logger.warning(f"Scheduler não iniciado: {e}")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)

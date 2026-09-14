from celery import Celery

from app.shared.config.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "system_automation",
    broker=settings.effective_celery_broker_url,
    backend=settings.effective_celery_result_backend,
    include=[
        "app.workers.tasks.ping",
        "app.workers.tasks.durable_jobs",
        "app.workers.tasks.ingestao",
        "app.workers.tasks.comunicacoes",
        "app.workers.tasks.pedidos",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_default_queue="celery",
    task_create_missing_queues=True,
    task_routes={
        "app.workers.tasks.pedidos.processar_pedidos": {
            "queue": "orders_processing",
        },
    },
    timezone="America/Sao_Paulo",
    enable_utc=True,
)

# Agenda periódica: full refresh, sweeper do ledger durável e delivery outbox.
# O full refresh usa INGESTAO_INTERVALO_SEGUNDOS (default 7200 s = 2 horas).
celery_app.conf.beat_schedule = {
    "sincronizar-databricks": {
        "task": "app.workers.tasks.ingestao.sincronizar_databricks",
        "schedule": float(settings.ingestao_intervalo_segundos),
    },
    "reconciliar-jobs-duraveis": {
        "task": "app.workers.tasks.durable_jobs.reconciliar_jobs",
        "schedule": 60.0,
    },
    "reconciliar-entregas-comunicacoes": {
        "task": "app.workers.tasks.comunicacoes.processar_entregas",
        "schedule": float(settings.communication_delivery_interval_seconds),
    },
}

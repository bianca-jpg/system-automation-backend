from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.ping.ping")
def ping() -> str:
    return "pong"

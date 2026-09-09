from celery import Celery

from app.config.setting import get_settings

settings = get_settings()

celery_app = Celery(
    "simlexx",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_concurrency=settings.MAX_WORKERS,
)

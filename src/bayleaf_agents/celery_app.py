from celery import Celery

from .config import settings

celery_app = Celery(
    "bayleaf_agents",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["bayleaf_agents.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_eager_propagates=settings.CELERY_TASK_ALWAYS_EAGER,
)

from __future__ import annotations

from celery import Celery

from ..config import settings
from ..services.agent_requests import process_agent_request

celery_app = Celery(
    "bayleaf_agents",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)


@celery_app.task(name="bayleaf_agents.process_agent_request")
def process_agent_request_task(agent_request_id: str, principal_payload: dict) -> None:
    process_agent_request(agent_request_id, principal_payload)

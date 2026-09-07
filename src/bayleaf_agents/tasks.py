import inspect
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Type

import structlog

from .auth.deps import Principal
from .celery_app import celery_app
from .db import SessionLocal
from .models import AgentRequest, AgentRequestState
from .agents.base_agent import BaseAgent
from .services.agent_registry import discover_agents
from .services.factories import (
    get_bayleaf,
    get_decider_provider,
    get_documents_tools,
    get_phi_filter,
    get_provider,
)

log = structlog.get_logger("agent_tasks")

_AGENT_CLASSES: Optional[Dict[str, Type[BaseAgent]]] = None


def _agent_classes() -> Dict[str, Type[BaseAgent]]:
    global _AGENT_CLASSES
    if _AGENT_CLASSES is None:
        _AGENT_CLASSES = discover_agents()
    return _AGENT_CLASSES


def _build_agent(agent_slug: str) -> BaseAgent:
    agent_cls = _agent_classes().get(agent_slug)
    if agent_cls is None:
        raise ValueError(f"unknown_agent_slug:{agent_slug}")
    common_kwargs = {
        "provider": get_provider(),
        "bayleaf": get_bayleaf(),
        "phi_filter": get_phi_filter(),
        "documents_tools": get_documents_tools(),
        "decider_provider": get_decider_provider(),
    }
    init_params = inspect.signature(agent_cls.__init__).parameters
    accepted = {k: v for k, v in common_kwargs.items() if k in init_params}
    return agent_cls(**accepted)


def _principal_from_payload(data: Optional[Dict[str, Any]]) -> Optional[Principal]:
    if not data:
        return None
    return Principal(
        user_id=data.get("user_id"),
        sub=data.get("sub"),
        scopes=data.get("scopes") or [],
        patient_id=data.get("patient_id"),
        raw=data.get("raw") or {},
        raw_token=data.get("raw_token") or "",
    )


@celery_app.task(name="bayleaf_agents.process_chat")
def process_chat_task(agent_request_id: str, payload: Dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        agent_request = db.query(AgentRequest).filter(AgentRequest.id == agent_request_id).first()
        if not agent_request:
            log.error("agent_request_missing", agent_request_id=agent_request_id)
            return
        if agent_request.state == AgentRequestState.cancelled:
            log.info("agent_request_cancelled", agent_request_id=agent_request_id)
            return

        try:
            agent = _build_agent(payload["agent_slug"])
        except Exception as exc:
            agent_request.state = AgentRequestState.failed
            agent_request.error_message = str(exc)
            agent_request.finished_at = datetime.now(timezone.utc)
            db.add(agent_request)
            db.commit()
            log.error("agent_request_failed", agent_request_id=agent_request_id, error=str(exc))
            return

        agent._process_chat(
            db=db,
            channel=payload["channel"],
            user_message=payload["user_message"],
            conversation_id=payload["conversation_id"],
            principal=_principal_from_payload(payload.get("principal")),
            lang=payload.get("lang") or "en-US",
            candidate_document_ids=payload.get("candidate_document_ids"),
            document_route_trace=payload.get("document_route_trace"),
            agent_slug=payload["agent_slug"],
            group_id=payload.get("group_id"),
            group_context=payload.get("group_context"),
            forced_document_ids=payload.get("forced_document_ids"),
            agent_request_id=agent_request_id,
        )
    except Exception:
        log.exception("process_chat_task_failed", agent_request_id=agent_request_id)
    finally:
        db.close()

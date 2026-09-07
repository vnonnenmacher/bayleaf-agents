from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from ..auth.deps import Principal
from ..db import SessionLocal
from ..models import AgentRequest, AgentRequestState, Message, Role
from .agent_registry import discover_agents
from .factories import (
    get_bayleaf,
    get_decider_provider,
    get_documents_tools,
    get_phi_filter,
    get_provider,
)

log = structlog.get_logger("agent")


def principal_to_payload(principal: Principal) -> dict[str, Any]:
    return {
        "user_id": principal.user_id,
        "sub": principal.sub,
        "scopes": list(principal.scopes or []),
        "patient_id": principal.patient_id,
        "raw": dict(principal.raw or {}),
        "raw_token": principal.raw_token,
    }


def payload_to_principal(payload: dict[str, Any]) -> Principal:
    return Principal(
        user_id=payload.get("user_id"),
        sub=payload.get("sub"),
        scopes=list(payload.get("scopes") or []),
        patient_id=payload.get("patient_id"),
        raw=dict(payload.get("raw") or {}),
        raw_token=str(payload.get("raw_token") or ""),
    )


def serialize_message(message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role.value,
        "content": message.content,
        "redacted_content": message.redacted_content,
        "tool_name": message.tool_name,
        "tool_args": message.tool_args,
        "tool_result": message.tool_result,
        "retrieval_trace": message.retrieval_trace,
        "cited_documents": message.cited_documents or [],
        "citations": message.citations or [],
        "created_at": message.created_at,
    }


def serialize_agent_request(db: Session, request: AgentRequest) -> dict[str, Any]:
    rows = (
        db.query(Message)
        .filter(Message.agent_request_id == request.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )
    return {
        "id": request.id,
        "conversation_id": request.conversation.external_id or request.conversation_id,
        "user_id": request.user_id,
        "agent_slug": request.agent_slug,
        "channel": request.channel,
        "state": request.state.value,
        "error_message": request.error_message,
        "created_at": request.created_at,
        "started_at": request.started_at,
        "finished_at": request.finished_at,
        "cancelled_at": request.cancelled_at,
        "messages": [serialize_message(item) for item in rows],
    }


def enqueue_agent_request(agent_request_id: str, principal_payload: dict[str, Any]) -> str:
    from ..workers.tasks import process_agent_request_task

    job = process_agent_request_task.delay(agent_request_id, principal_payload)
    return str(job.id)


def revoke_agent_request(task_id: str) -> None:
    if not task_id:
        return
    from ..workers.tasks import celery_app

    celery_app.control.revoke(task_id, terminate=True)


def process_agent_request(agent_request_id: str, principal_payload: dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        request = db.query(AgentRequest).filter(AgentRequest.id == agent_request_id).first()
        if not request:
            log.warning("agent_request_missing", agent_request_id=agent_request_id)
            return
        if request.state == AgentRequestState.cancelled:
            return

        request.state = AgentRequestState.processing
        request.started_at = request.started_at or datetime.utcnow()
        request.error_message = None
        db.add(request)
        db.commit()
        db.refresh(request)

        principal = payload_to_principal(principal_payload)
        agent_classes = discover_agents()
        agent_cls = agent_classes.get(request.agent_slug)
        if not agent_cls:
            raise RuntimeError(f"unknown_agent_slug:{request.agent_slug}")

        common_kwargs = {
            "provider": get_provider(),
            "bayleaf": get_bayleaf(),
            "phi_filter": get_phi_filter(),
            "documents_tools": get_documents_tools(),
            "decider_provider": get_decider_provider(),
        }
        init_params = inspect.signature(agent_cls.__init__).parameters
        accepted = {k: v for k, v in common_kwargs.items() if k in init_params}
        agent = agent_cls(**accepted)

        user_row = (
            db.query(Message)
            .filter(
                Message.agent_request_id == request.id,
                Message.role == Role.user,
            )
            .order_by(Message.created_at.asc(), Message.id.asc())
            .first()
        )

        agent._process_chat(
            db=db,
            channel=request.channel,
            user_message=request.user_message,
            external_conversation_id=request.conversation.external_id or request.conversation_id,
            principal=principal,
            lang=request.lang,
            agent_slug=request.agent_slug,
            group_id=request.conversation.group_id,
            group_context=request.group_context or None,
            forced_document_ids=request.forced_document_ids or None,
            persist_user_message=False,
            user_message_id=(user_row.id if user_row else None),
            agent_request_id=request.id,
        )

        db.refresh(request)
        if request.state != AgentRequestState.cancelled:
            request.state = AgentRequestState.succeeded
            request.finished_at = datetime.utcnow()
            db.add(request)
            db.commit()

    except Exception as exc:
        db.rollback()
        request = db.query(AgentRequest).filter(AgentRequest.id == agent_request_id).first()
        if request and request.state != AgentRequestState.cancelled:
            request.state = AgentRequestState.failed
            request.error_message = str(exc)
            request.finished_at = datetime.utcnow()
            db.add(request)
            db.commit()
        log.exception("agent_request_failed", agent_request_id=agent_request_id)
    finally:
        db.close()

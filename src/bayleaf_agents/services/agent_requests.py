from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import AgentRequest, AgentRequestState, Message, Role
from ..schemas.chat import AgentRequestMessage, AgentRequestResponse, Citation, ResearchDocument

log = structlog.get_logger("agent_requests")


def find_active_agent_request_for_conversation(db: Session, conversation_id: str, user_id: str) -> Optional[AgentRequest]:
    # user_id is filtered explicitly so a conflict in one user's conversation can never block another user.
    return (
        db.query(AgentRequest)
        .join(Message, Message.agent_request_id == AgentRequest.id)
        .filter(
            Message.conversation_id == conversation_id,
            AgentRequest.user_id == user_id,
            AgentRequest.state.in_([AgentRequestState.waiting, AgentRequestState.processing]),
        )
        .first()
    )


def get_owned_agent_request(db: Session, *, agent_request_id: str, user_id: str) -> AgentRequest:
    agent_request = (
        db.query(AgentRequest)
        .filter(AgentRequest.id == agent_request_id, AgentRequest.user_id == user_id)
        .first()
    )
    if not agent_request:
        raise HTTPException(status_code=404, detail="agent_request_not_found")
    return agent_request


def cancel_agent_request(db: Session, agent_request: AgentRequest) -> AgentRequest:
    if agent_request.state in (AgentRequestState.waiting, AgentRequestState.processing):
        agent_request.state = AgentRequestState.cancelled
        agent_request.cancelled_at = datetime.now(timezone.utc)
        db.add(agent_request)
        db.commit()
        db.refresh(agent_request)
        log.info("agent_request_cancelled", agent_request_id=agent_request.id)
    return agent_request


def _conversation_id_for_request(db: Session, agent_request_id: str) -> Optional[str]:
    message = (
        db.query(Message)
        .filter(Message.agent_request_id == agent_request_id, Message.conversation_id.isnot(None))
        .first()
    )
    return message.conversation_id if message else None


def retry_agent_request(db: Session, original: AgentRequest) -> AgentRequest:
    if original.state not in (AgentRequestState.failed, AgentRequestState.cancelled):
        raise ValueError("retry_not_allowed")

    payload: Dict[str, Any] = dict(original.payload or {})
    conversation_id = payload.get("conversation_id") or _conversation_id_for_request(db, original.id)
    if conversation_id and find_active_agent_request_for_conversation(db, conversation_id, original.user_id):
        raise ValueError("active_request_conflict")

    new_request = AgentRequest(
        user_id=original.user_id,
        agent_slug=original.agent_slug,
        channel=original.channel,
        state=AgentRequestState.waiting,
        payload=payload,
    )
    db.add(new_request)
    db.commit()
    db.refresh(new_request)

    db.add(
        Message(
            agent_request_id=new_request.id,
            conversation_id=conversation_id,
            role=Role.user,
            content=payload.get("user_message", ""),
        )
    )
    db.commit()

    log.info("agent_request_created", agent_request_id=new_request.id, retry_of=original.id)
    schedule_process_chat(new_request.id, payload)
    return new_request


def schedule_process_chat(agent_request_id: str, payload: Dict[str, Any]) -> None:
    # Imported lazily to avoid a circular import (tasks -> agent_registry -> agents.base_agent).
    from ..tasks import process_chat_task

    process_chat_task.delay(agent_request_id, payload)


def _message_item(message: Message) -> AgentRequestMessage:
    return AgentRequestMessage(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role.value,
        content=message.content,
        redacted_content=message.redacted_content,
        tool_name=message.tool_name,
        tool_args=message.tool_args,
        tool_result=message.tool_result,
        retrieval_trace=message.retrieval_trace,
        cited_documents=[ResearchDocument(**d) for d in (message.cited_documents or [])],
        citations=[Citation(**c) for c in (message.citations or [])],
        created_at=message.created_at,
    )


def serialize_agent_request(db: Session, agent_request: AgentRequest) -> AgentRequestResponse:
    rows: List[Message] = (
        db.query(Message)
        .filter(Message.agent_request_id == agent_request.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    conversation_id = next((m.conversation_id for m in rows if m.conversation_id), None)
    return AgentRequestResponse(
        id=agent_request.id,
        user_id=agent_request.user_id,
        agent_slug=agent_request.agent_slug,
        channel=agent_request.channel,
        conversation_id=conversation_id,
        state=agent_request.state.value,
        error_message=agent_request.error_message,
        created_at=agent_request.created_at,
        started_at=agent_request.started_at,
        finished_at=agent_request.finished_at,
        cancelled_at=agent_request.cancelled_at,
        messages=[_message_item(m) for m in rows],
    )

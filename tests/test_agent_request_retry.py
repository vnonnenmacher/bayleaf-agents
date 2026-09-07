import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bayleaf_agents.models import AgentRequest, AgentRequestState, Base, Conversation, Message, Role
from bayleaf_agents.services import agent_requests as agent_requests_service
from bayleaf_agents.services.agent_requests import retry_agent_request


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _failed_request_with_conversation(db, conversation_id: str) -> AgentRequest:
    request = AgentRequest(
        user_id="user-1",
        agent_slug="treatment",
        channel="bayleaf_app",
        state=AgentRequestState.failed,
        error_message="boom",
        payload={"user_message": "hi", "channel": "bayleaf_app", "agent_slug": "treatment"},
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    db.add(
        Message(
            conversation_id=conversation_id,
            agent_request_id=request.id,
            role=Role.user,
            content="hi",
        )
    )
    db.commit()
    return request


def test_retry_agent_request_rejects_when_active_request_exists_for_conversation(monkeypatch):
    db = _session()
    monkeypatch.setattr(agent_requests_service, "schedule_process_chat", lambda *args, **kwargs: None)

    conv = Conversation(user_id="user-1", channel="bayleaf_app", agent_slug="treatment")
    db.add(conv)
    db.commit()
    db.refresh(conv)

    original = _failed_request_with_conversation(db, conv.id)

    active_request = AgentRequest(
        user_id="user-1",
        agent_slug="treatment",
        channel="bayleaf_app",
        state=AgentRequestState.processing,
    )
    db.add(active_request)
    db.commit()
    db.refresh(active_request)
    db.add(
        Message(
            conversation_id=conv.id,
            agent_request_id=active_request.id,
            role=Role.user,
            content="still going",
        )
    )
    db.commit()

    with pytest.raises(ValueError, match="active_request_conflict"):
        retry_agent_request(db, original)


def test_retry_agent_request_succeeds_when_no_active_request_for_conversation(monkeypatch):
    db = _session()
    scheduled = []
    monkeypatch.setattr(
        agent_requests_service,
        "schedule_process_chat",
        lambda agent_request_id, payload: scheduled.append(agent_request_id),
    )

    conv = Conversation(user_id="user-1", channel="bayleaf_app", agent_slug="treatment")
    db.add(conv)
    db.commit()
    db.refresh(conv)

    original = _failed_request_with_conversation(db, conv.id)

    new_request = retry_agent_request(db, original)

    assert new_request.id != original.id
    assert new_request.state == AgentRequestState.waiting
    assert scheduled == [new_request.id]

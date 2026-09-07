import base64
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bayleaf_agents.app import create_app
from bayleaf_agents.auth.deps import Principal
from bayleaf_agents.db import get_db
from bayleaf_agents.models import AgentRequest, AgentRequestState, Base, Conversation, Message, Role
from bayleaf_agents.routers import agents as agents_router
from bayleaf_agents.services.agent_requests import principal_to_payload, process_agent_request


def _jwt(claims: dict) -> str:
    payload = json.dumps(claims, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
    return f"header.{encoded}.signature"


def _auth_headers(claims: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(claims)}"}


def _client_with_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    app = create_app()

    def _override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app), Session


def test_chat_submit_returns_agent_request_and_initial_message(monkeypatch):
    client, _ = _client_with_db()

    monkeypatch.setattr(agents_router, "request_scheduler", lambda request_id, principal_payload: "job-1")

    resp = client.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "hello", "lang": "en-US"},
        headers=_auth_headers({"user_id": "user-1"}),
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["state"] == "waiting"
    assert body["agent_slug"] == "treatment"
    assert body["channel"] == "bayleaf_app"
    assert body["messages"]
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"] == "hello"


def test_chat_submit_enforces_one_active_request_per_conversation(monkeypatch):
    client, _ = _client_with_db()
    monkeypatch.setattr(agents_router, "request_scheduler", lambda request_id, principal_payload: "job-1")

    first = client.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "first", "lang": "en-US"},
        headers=_auth_headers({"user_id": "user-1"}),
    )
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]

    second = client.post(
        "/agents/treatment/chat",
        json={
            "channel": "bayleaf_app",
            "conversation_id": conversation_id,
            "message": "second",
            "lang": "en-US",
        },
        headers=_auth_headers({"user_id": "user-1"}),
    )
    assert second.status_code == 409
    assert second.json() == {"detail": "active_request_exists"}


def test_get_request_includes_internal_messages_by_default(monkeypatch):
    client, Session = _client_with_db()
    monkeypatch.setattr(agents_router, "request_scheduler", lambda request_id, principal_payload: "job-1")

    created = client.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "hello", "lang": "en-US"},
        headers=_auth_headers({"user_id": "user-1"}),
    )
    assert created.status_code == 200
    request_id = created.json()["id"]

    db = Session()
    try:
        row = db.query(AgentRequest).filter(AgentRequest.id == request_id).first()
        assert row is not None
        db.add(
            Message(
                conversation_id=row.conversation_id,
                agent_request_id=row.id,
                role=Role.tool,
                tool_name="query_documents",
                content='{"ok":true}',
                redacted_content='{"ok":true}',
                tool_result={"ok": True},
            )
        )
        db.commit()
    finally:
        db.close()

    resp = client.get(
        f"/agents/requests/{request_id}",
        headers=_auth_headers({"user_id": "user-1"}),
    )
    assert resp.status_code == 200
    roles = [item["role"] for item in resp.json()["messages"]]
    assert "tool" in roles


def test_cancel_and_retry_request_flow(monkeypatch):
    client, _ = _client_with_db()
    monkeypatch.setattr(agents_router, "request_scheduler", lambda request_id, principal_payload: "job-1")

    created = client.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "hello", "lang": "en-US"},
        headers=_auth_headers({"user_id": "user-1"}),
    )
    assert created.status_code == 200
    request_id = created.json()["id"]

    cancelled = client.post(
        f"/agents/requests/{request_id}/cancel",
        headers=_auth_headers({"user_id": "user-1"}),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"

    retried = client.post(
        f"/agents/requests/{request_id}/retry",
        headers=_auth_headers({"user_id": "user-1"}),
    )
    assert retried.status_code == 200
    retry_body = retried.json()
    assert retry_body["id"] != request_id
    assert retry_body["state"] == "waiting"


def test_process_agent_request_marks_succeeded(monkeypatch):
    _, Session = _client_with_db()
    req_id = None

    db = Session()
    try:
        conv = Conversation(
            user_id="user-1",
            channel="bayleaf_app",
            agent_slug="treatment",
            name="Conv",
        )
        db.add(conv)
        db.commit()
        db.refresh(conv)

        req = AgentRequest(
            conversation_id=conv.id,
            user_id="user-1",
            agent_slug="treatment",
            channel="bayleaf_app",
            state=AgentRequestState.waiting,
            user_message="hello",
            lang="en-US",
        )
        db.add(req)
        db.commit()
        db.refresh(req)
        req_id = req.id

        db.add(
            Message(
                conversation_id=conv.id,
                agent_request_id=req_id,
                role=Role.user,
                content="hello",
            )
        )
        db.commit()
    finally:
        db.close()

    from bayleaf_agents.services import agent_requests

    monkeypatch.setattr(agent_requests, "SessionLocal", Session)
    process_agent_request(
        req_id,
        principal_to_payload(
            Principal(
                user_id="user-1",
                sub="user-1",
                scopes=[],
                patient_id=None,
                raw={},
                raw_token="token",
            )
        ),
    )

    db = Session()
    try:
        saved = db.query(AgentRequest).filter(AgentRequest.id == req_id).first()
        assert saved is not None
        assert saved.state == AgentRequestState.succeeded

        assistant_rows = (
            db.query(Message)
            .filter(Message.agent_request_id == req_id, Message.role == Role.assistant)
            .all()
        )
        assert assistant_rows
    finally:
        db.close()

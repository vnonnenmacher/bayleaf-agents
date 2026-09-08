import base64
import json
import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bayleaf_agents.app import create_app
from bayleaf_agents.db import get_db
from bayleaf_agents.llm.mock import MockProvider
from bayleaf_agents.models import AgentRequest, AgentRequestState, Base
from bayleaf_agents.routers import agents as agents_router
from bayleaf_agents.services import agent_requests as agent_requests_service


def _jwt(claims: dict) -> str:
    payload = json.dumps(claims, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
    return f"header.{encoded}.signature"


def _auth_headers(user_id: str) -> dict[str, str]:
    claims = {"user_id": user_id, "exp": int(time.time()) + 3600}
    return {"Authorization": f"Bearer {_jwt(claims)}"}


def _client(monkeypatch):
    # avoid touching the real Celery/Redis broker; endpoint tests only assert on the
    # synchronous submit/retrieve/cancel/retry contract, not on worker execution.
    monkeypatch.setattr(agent_requests_service, "schedule_process_chat", lambda *args, **kwargs: None)

    # never depend on ambient LLM_PROVIDER/DECIDER_LLM_PROVIDER/OPENAI_API_KEY config;
    # chat() never calls the LLM, but agent construction still requires a provider instance.
    monkeypatch.setattr(agents_router, "get_provider", lambda: MockProvider())
    monkeypatch.setattr(agents_router, "get_decider_provider", lambda: MockProvider())

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


def test_chat_submit_returns_agent_request_with_conversation_id(monkeypatch):
    client, _ = _client(monkeypatch)

    r = client.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "Hello", "lang": "pt-BR"},
        headers=_auth_headers("user-1"),
    )

    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "user-1"
    assert body["state"] in ("waiting", "processing")
    assert body["conversation_id"]
    assert body["error_message"] is None
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"] == "Hello"
    assert body["messages"][0]["conversation_id"] == body["conversation_id"]


def test_chat_submit_conflicts_when_conversation_has_active_request(monkeypatch):
    client, _ = _client(monkeypatch)
    headers = _auth_headers("user-1")

    first = client.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "Hello"},
        headers=headers,
    )
    conv_id = first.json()["conversation_id"]

    second = client.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "Again", "conversation_id": conv_id},
        headers=headers,
    )

    assert second.status_code == 409
    assert second.json() == {"detail": "active_request_conflict"}


def test_get_agent_request_returns_submitted_request(monkeypatch):
    client, _ = _client(monkeypatch)
    headers = _auth_headers("user-1")

    submit = client.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "Hello"},
        headers=headers,
    )
    request_id = submit.json()["id"]

    r = client.get(f"/agents/requests/{request_id}", headers=headers)

    assert r.status_code == 200
    assert r.json()["id"] == request_id


def test_get_agent_request_not_found_for_other_user(monkeypatch):
    client, _ = _client(monkeypatch)

    submit = client.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "Hello"},
        headers=_auth_headers("user-1"),
    )
    request_id = submit.json()["id"]

    r = client.get(f"/agents/requests/{request_id}", headers=_auth_headers("user-2"))

    assert r.status_code == 404
    assert r.json() == {"detail": "agent_request_not_found"}


def test_cancel_agent_request_moves_to_cancelled_and_is_idempotent(monkeypatch):
    client, _ = _client(monkeypatch)
    headers = _auth_headers("user-1")

    submit = client.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "Hello"},
        headers=headers,
    )
    request_id = submit.json()["id"]

    r1 = client.post(f"/agents/requests/{request_id}/cancel", headers=headers)
    assert r1.status_code == 200
    assert r1.json()["state"] == "cancelled"

    # idempotent: already terminal, cancelling again is a no-op
    r2 = client.post(f"/agents/requests/{request_id}/cancel", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["state"] == "cancelled"


def test_cancel_agent_request_not_found_for_other_user(monkeypatch):
    client, _ = _client(monkeypatch)

    submit = client.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "Hello"},
        headers=_auth_headers("user-1"),
    )
    request_id = submit.json()["id"]

    r = client.post(f"/agents/requests/{request_id}/cancel", headers=_auth_headers("user-2"))

    assert r.status_code == 404
    assert r.json() == {"detail": "agent_request_not_found"}


def test_retry_agent_request_rejected_when_not_terminal(monkeypatch):
    client, _ = _client(monkeypatch)
    headers = _auth_headers("user-1")

    submit = client.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "Hello"},
        headers=headers,
    )
    request_id = submit.json()["id"]

    r = client.post(f"/agents/requests/{request_id}/retry", headers=headers)

    assert r.status_code == 409
    assert r.json() == {"detail": "retry_not_allowed"}


def test_retry_agent_request_creates_new_request_after_failure(monkeypatch):
    client, Session = _client(monkeypatch)
    headers = _auth_headers("user-1")

    submit = client.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "Hello"},
        headers=headers,
    )
    request_id = submit.json()["id"]
    conv_id = submit.json()["conversation_id"]

    # simulate the async worker having failed the request
    db = Session()
    try:
        agent_request = db.query(AgentRequest).filter(AgentRequest.id == request_id).first()
        agent_request.state = AgentRequestState.failed
        agent_request.error_message = "boom"
        db.add(agent_request)
        db.commit()
    finally:
        db.close()

    r = client.post(f"/agents/requests/{request_id}/retry", headers=headers)

    assert r.status_code == 200
    body = r.json()
    assert body["id"] != request_id
    assert body["state"] == "waiting"
    assert body["conversation_id"] == conv_id


def test_retry_agent_request_not_found_for_other_user(monkeypatch):
    client, Session = _client(monkeypatch)

    submit = client.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "Hello"},
        headers=_auth_headers("user-1"),
    )
    request_id = submit.json()["id"]

    db = Session()
    try:
        agent_request = db.query(AgentRequest).filter(AgentRequest.id == request_id).first()
        agent_request.state = AgentRequestState.failed
        db.add(agent_request)
        db.commit()
    finally:
        db.close()

    r = client.post(f"/agents/requests/{request_id}/retry", headers=_auth_headers("user-2"))

    assert r.status_code == 404
    assert r.json() == {"detail": "agent_request_not_found"}

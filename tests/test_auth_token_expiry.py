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
from bayleaf_agents.models import Base
from bayleaf_agents.routers import agents as agents_router
from bayleaf_agents.services import agent_requests as agent_requests_service


def _unverified_jwt(payload: dict) -> str:
    def _b64(data: dict) -> str:
        raw = json.dumps(data).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    header = _b64({"alg": "none", "typ": "JWT"})
    body = _b64(payload)
    return f"{header}.{body}.sig"


def test_chat_rejects_expired_token_before_submitting_agent_request():
    c = TestClient(create_app())
    expired_token = _unverified_jwt({"user_id": "349", "exp": int(time.time()) - 60})

    r = c.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "Ola", "lang": "pt-BR"},
        headers={"Authorization": f"Bearer {expired_token}"},
    )

    assert r.status_code == 401
    assert r.json() == {"detail": {"error": "token_expired"}}


def test_chat_accepts_token_with_future_expiry(monkeypatch):
    # never depend on ambient LLM_PROVIDER/DECIDER_LLM_PROVIDER/OPENAI_API_KEY config;
    # this test only cares that auth doesn't reject a valid token, not LLM behavior.
    monkeypatch.setattr(agents_router, "get_provider", lambda: MockProvider())
    monkeypatch.setattr(agents_router, "get_decider_provider", lambda: MockProvider())

    # avoid a live Redis/Celery broker dependency too.
    monkeypatch.setattr(agent_requests_service, "schedule_process_chat", lambda *args, **kwargs: None)

    # isolate from the real DATABASE_URL; this test shouldn't depend on a live postgres service.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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

    c = TestClient(app)
    valid_token = _unverified_jwt({"user_id": "349", "exp": int(time.time()) + 3600})

    r = c.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "Ola", "lang": "pt-BR"},
        headers={"Authorization": f"Bearer {valid_token}"},
    )

    assert r.status_code != 401

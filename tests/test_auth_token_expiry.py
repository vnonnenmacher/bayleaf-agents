import base64
import json
import time

from fastapi.testclient import TestClient

from bayleaf_agents.app import create_app
from bayleaf_agents.llm.mock import MockProvider
from bayleaf_agents.routers import agents as agents_router


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

    c = TestClient(create_app())
    valid_token = _unverified_jwt({"user_id": "349", "exp": int(time.time()) + 3600})

    r = c.post(
        "/agents/treatment/chat",
        json={"channel": "bayleaf_app", "message": "Ola", "lang": "pt-BR"},
        headers={"Authorization": f"Bearer {valid_token}"},
    )

    assert r.status_code != 401

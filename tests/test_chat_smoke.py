from fastapi.testclient import TestClient
from bayleaf_agents.app import create_app


def test_chat_smoke():
    c = TestClient(create_app())
    payload = {
        "channel": "bayleaf_app",
        "message": "Estou com náusea e tomei meus remédios hoje.",
        "lang": "pt-BR",
    }
    r = c.post("/agents/treatment/chat", json=payload)
    assert r.status_code == 401
    assert r.json() == {"detail": "missing_token"}

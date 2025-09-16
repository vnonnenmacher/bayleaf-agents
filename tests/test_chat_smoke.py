from fastapi.testclient import TestClient
from bayleaf_agents.app import create_app


def test_chat_smoke():
    c = TestClient(create_app())
    payload = {
        "channel": "bayleaf_app",
        "patient_id": "uuid-demo",
        "message": "Estou com náusea e tomei meus remédios hoje.",
        "locale": "pt-BR",
        "metadata": {}
    }
    r = c.post("/chat", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert "reply" in data and "trace_id" in data

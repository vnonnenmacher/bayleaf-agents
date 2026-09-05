from fastapi.testclient import TestClient

from bayleaf_agents.app import create_app
from bayleaf_agents.config import settings


def test_cors_allows_localhost_with_dynamic_port():
    previous = settings.ALLOWED_HOSTS
    settings.ALLOWED_HOSTS = "localhost,127.0.0.1,labcopilot.nonnenmacher.tech"
    try:
        client = TestClient(create_app())
        origin = "http://localhost:37437"
        response = client.options(
            "/agents/conversations?limit=20&offset=0",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == origin
    finally:
        settings.ALLOWED_HOSTS = previous

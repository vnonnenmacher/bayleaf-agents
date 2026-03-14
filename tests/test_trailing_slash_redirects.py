import base64
import json

from fastapi.testclient import TestClient

from bayleaf_agents.app import create_app


def _jwt(claims: dict) -> str:
    payload = json.dumps(claims, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
    return f"header.{encoded}.signature"


def _auth_headers(claims: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(claims)}"}


def test_health_trailing_slash_redirects_to_canonical():
    c = TestClient(create_app())
    r = c.get("/health/", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/health"


def test_agents_endpoint_trailing_slash_redirects_to_canonical():
    c = TestClient(create_app())
    r = c.get(
        "/agents/user-metadata/?foo=bar",
        headers=_auth_headers({"user_id": "user-1"}),
        follow_redirects=False,
    )
    assert r.status_code == 308
    assert r.headers["location"] == "/agents/user-metadata?foo=bar"

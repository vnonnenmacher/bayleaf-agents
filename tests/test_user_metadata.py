import base64
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bayleaf_agents.app import create_app
from bayleaf_agents.db import get_db
from bayleaf_agents.models import Base


def _jwt(claims: dict) -> str:
    payload = json.dumps(claims, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
    return f"header.{encoded}.signature"


def _auth_headers(claims: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(claims)}"}


def _client() -> TestClient:
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
    return TestClient(app)


def test_user_metadata_upsert_and_get():
    c = _client()
    headers = _auth_headers({"user_id": "user-1"})

    create_resp = c.put(
        "/agents/user-metadata",
        json={"metadata": {"theme": "dark", "notifications": {"email": True}}},
        headers=headers,
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["owner_id"] == "user-1"
    assert created["metadata"]["theme"] == "dark"

    update_resp = c.put(
        "/agents/user-metadata",
        json={"metadata": {"theme": "light"}},
        headers=headers,
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["owner_id"] == "user-1"
    assert updated["metadata"] == {"theme": "light"}
    assert updated["updated_at"] >= created["updated_at"]

    get_resp = c.get("/agents/user-metadata", headers=headers)
    assert get_resp.status_code == 200
    fetched = get_resp.json()
    assert fetched["metadata"] == {"theme": "light"}


def test_user_metadata_get_creates_empty_record_for_new_user():
    c = _client()
    c.put(
        "/agents/user-metadata",
        json={"metadata": {"timezone": "UTC"}},
        headers=_auth_headers({"user_id": "user-1"}),
    )

    r = c.get(
        "/agents/user-metadata",
        headers=_auth_headers({"user_id": "user-2"}),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["owner_id"] == "user-2"
    assert body["metadata"] == {}

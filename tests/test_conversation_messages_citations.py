import base64
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bayleaf_agents.app import create_app
from bayleaf_agents.db import get_db
from bayleaf_agents.models import Base, Conversation, Message, Role


def _jwt(claims: dict) -> str:
    payload = json.dumps(claims, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
    return f"header.{encoded}.signature"


def _auth_headers(claims: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(claims)}"}


def _client():
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


def test_list_conversation_messages_includes_citations_and_cited_documents():
    client, Session = _client()
    db = Session()
    conv_id = None
    try:
        conv = Conversation(
            user_id="user-1",
            channel="bayleaf_app",
            agent_slug="labcopilot",
            name="Research conversation",
        )
        db.add(conv)
        db.commit()
        db.refresh(conv)
        conv_id = conv.id

        db.add(
            Message(
                conversation_id=conv.id,
                role=Role.user,
                content="What should I do for mild headaches?",
            )
        )
        db.add(
            Message(
                conversation_id=conv.id,
                role=Role.assistant,
                content="Legacy answer with no stored citations.",
            )
        )
        db.add(
            Message(
                conversation_id=conv.id,
                role=Role.assistant,
                content="Use hydration and rest.",
                cited_documents=[{"name": "Clinical Guide", "uuid": "doc-1"}],
                citations=[
                    {
                        "id": "c1",
                        "document_uuid": "doc-1",
                        "document_name": "Clinical Guide",
                        "chunk_ref": "doc-1#0",
                        "evidence_text": "Hydration and rest are recommended in mild headache cases.",
                        "retrieval_score": 0.95,
                    }
                ],
            )
        )
        db.commit()
    finally:
        db.close()

    assert conv_id is not None
    r = client.get(
        f"/agents/conversations/{conv_id}/messages",
        headers=_auth_headers({"user_id": "user-1"}),
    )
    assert r.status_code == 200
    body = r.json()
    items_by_content = {item["content"]: item for item in body["items"]}

    assert items_by_content["Use hydration and rest."]["cited_documents"] == [
        {"name": "Clinical Guide", "uuid": "doc-1"}
    ]
    assert items_by_content["Use hydration and rest."]["citations"] == [
        {
            "id": "c1",
            "document_uuid": "doc-1",
            "document_name": "Clinical Guide",
            "chunk_ref": "doc-1#0",
            "evidence_text": "Hydration and rest are recommended in mild headache cases.",
            "retrieval_score": 0.95,
        }
    ]
    assert items_by_content["Legacy answer with no stored citations."]["cited_documents"] == []
    assert items_by_content["Legacy answer with no stored citations."]["citations"] == []

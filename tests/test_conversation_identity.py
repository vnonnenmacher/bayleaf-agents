from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bayleaf_agents.agents.base_agent import BaseAgent
from bayleaf_agents.llm.mock import MockProvider
import pytest

from fastapi import HTTPException

from bayleaf_agents.models import Base, Conversation, ConversationGroup, ConversationGroupType
from bayleaf_agents.routers.agents import _resolve_owned_group, _resolve_user_conversation
from bayleaf_agents.tools.bayleaf import BayleafClient


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _agent() -> BaseAgent:
    return BaseAgent(
        name="test-agent",
        objective="test objective",
        provider=MockProvider(),
        bayleaf=BayleafClient("http://example.test"),
        use_phi_filter=False,
    )


def test_get_or_create_conversation_reuses_internal_id_when_conversation_id_is_returned():
    db = _session()
    agent = _agent()

    conv1 = agent._get_or_create_conversation(
        db,
        external_id=None,
        user_id="user-1",
        channel="bayleaf_app",
        agent_slug="labcopilot",
    )
    conv2 = agent._get_or_create_conversation(
        db,
        external_id=conv1.id,
        user_id="user-1",
        channel="bayleaf_app",
        agent_slug="labcopilot",
    )

    assert conv2.id == conv1.id
    assert db.query(Conversation).count() == 1


def test_resolve_user_conversation_prefers_internal_id_over_external_id_collision():
    db = _session()

    conv1 = Conversation(
        external_id=None,
        user_id="user-1",
        channel="bayleaf_app",
        agent_slug="labcopilot",
    )
    db.add(conv1)
    db.commit()
    db.refresh(conv1)

    # Simulate legacy duplicate conversation created with external_id == conv1.id
    conv2 = Conversation(
        external_id=conv1.id,
        user_id="user-1",
        channel="bayleaf_app",
        agent_slug="labcopilot",
    )
    db.add(conv2)
    db.commit()

    resolved = _resolve_user_conversation(
        db,
        user_id="user-1",
        conversation_identifier=conv1.id,
        channel="bayleaf_app",
        agent_slug="labcopilot",
    )

    assert resolved is not None
    assert resolved.id == conv1.id


def test_get_or_create_conversation_rejects_group_mismatch():
    db = _session()
    agent = _agent()

    conv = agent._get_or_create_conversation(
        db,
        external_id="conv-external",
        user_id="user-1",
        channel="bayleaf_app",
        agent_slug="labcopilot",
        group_id="group-1",
    )

    with pytest.raises(ValueError, match="conversation_group_mismatch"):
        agent._get_or_create_conversation(
            db,
            external_id=conv.id,
            user_id="user-1",
            channel="bayleaf_app",
            agent_slug="labcopilot",
            group_id="group-2",
        )


def test_resolve_owned_group_enforces_owner():
    db = _session()
    group = ConversationGroup(
        owner_id="user-1",
        type=ConversationGroupType.project,
        metadata_json={"description": "proj"},
        document_uuids=["doc-1"],
        is_active=True,
    )
    db.add(group)
    db.commit()
    db.refresh(group)

    resolved = _resolve_owned_group(db, owner_id="user-1", group_id=group.id)
    assert resolved.id == group.id

    with pytest.raises(HTTPException) as exc_info:
        _resolve_owned_group(db, owner_id="user-2", group_id=group.id)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "group_not_found"


def test_get_or_create_conversation_sets_name_from_initial_message():
    db = _session()
    agent = _agent()

    first_message = "Need appointment for severe back pain"
    conv = agent._get_or_create_conversation(
        db,
        external_id=None,
        user_id="user-1",
        channel="bayleaf_app",
        agent_slug="labcopilot",
        initial_name=agent._conversation_title_from_first_message(first_message),
    )

    assert conv.name == "Need appointment for severe back pain"

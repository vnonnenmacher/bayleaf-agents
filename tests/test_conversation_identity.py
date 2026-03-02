from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bayleaf_agents.agents.base_agent import BaseAgent
from bayleaf_agents.llm.mock import MockProvider
from bayleaf_agents.models import Base, Conversation
from bayleaf_agents.routers.agents import _resolve_user_conversation
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


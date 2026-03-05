from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bayleaf_agents.agents.base_agent import BaseAgent
from bayleaf_agents.auth.deps import Principal
from bayleaf_agents.llm.base import LLMProvider
from bayleaf_agents.models import Base
from bayleaf_agents.tools.bayleaf import BayleafClient


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


class QueryDocumentsProvider(LLMProvider):
    name = "query-documents-provider"

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if tools:
            return {
                "reply": "",
                "tool_calls": [
                    {"id": "call_1", "name": "query_documents", "args": {"query": "headache treatment"}}
                ],
            }
        return {"reply": "Use hydration and rest.", "tool_calls": []}


class StubDocumentsTools:
    def query_documents(self, **kwargs):
        return {
            "chunks": [
                {"document_uuid": "doc-1", "name": "Clinical Guide"},
                {"document_uuid": "doc-1", "name": "Clinical Guide"},
                {"document_uuid": "doc-2", "name": "Medication Reference"},
                {"document_uuid": "doc-3", "name": ""},
            ],
            "trace": {"trace_id": "retr_demo"},
        }


def test_chat_returns_research_documents_from_query_results():
    db = _session()
    provider = QueryDocumentsProvider()
    agent = BaseAgent(
        name="test-agent",
        objective="test objective",
        provider=provider,
        bayleaf=BayleafClient("http://example.test"),
        documents_tools=StubDocumentsTools(),
        use_phi_filter=False,
    )
    principal = Principal(
        user_id="user-1",
        sub="user-1",
        scopes=["chat.send"],
        patient_id=None,
        raw={},
        raw_token="token",
    )

    result = agent.chat(
        db=db,
        channel="bayleaf_app",
        user_message="I have a headache.",
        external_conversation_id=None,
        principal=principal,
        agent_slug="labcopilot",
    )

    assert result["research_documents"] == [
        {"name": "Clinical Guide", "uuid": "doc-1"},
        {"name": "Medication Reference", "uuid": "doc-2"},
    ]

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bayleaf_agents.agents.reasoning.base_agent import ReasoningBaseAgent
from bayleaf_agents.auth.deps import Principal
from bayleaf_agents.llm.base import LLMProvider
from bayleaf_agents.models import Base, Message, Role
from bayleaf_agents.tools.bayleaf import BayleafClient


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


class MainProvider(LLMProvider):
    name = "main-provider"

    def chat(self, messages, tools):
        return {"reply": "ok", "tool_calls": []}


class DeciderNoRetrievalProvider(LLMProvider):
    name = "decider-no-retrieval"

    def chat(self, messages, tools):
        return {
            "reply": '{"needs_retrieval": false, "candidate_document_ids": [], "reason": "covered", "confidence": 0.7}',
            "tool_calls": [],
        }


class StubDocumentsTools:
    def __init__(self):
        self.calls = []

    def documents_available(self, **kwargs):
        return [
            {
                "uuid": "doc-1",
                "name": "Clinical Book",
                "source_type": "bayleaf",
                "is_bayleaf": True,
                "status": "indexed",
                "description": None,
                "indexed_at": "2026-03-05T00:00:00+00:00",
            }
        ]

    def query_documents(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "query": kwargs.get("query"),
            "chunks": [
                {
                    "document_uuid": "doc-1",
                    "name": "Clinical Book",
                    "score": 0.9,
                    "text_chunk": "O colesterol nao vem exclusivamente da alimentacao.",
                }
            ],
            "trace": {"trace_id": "retr_test_1"},
        }


class TestReasoningAgent(ReasoningBaseAgent):
    def __init__(self, *, provider, decider_provider, documents_tools):
        super().__init__(
            name="test-reasoning-agent",
            objective="test objective",
            provider=provider,
            bayleaf=BayleafClient("http://example.test"),
            documents_tools=documents_tools,
            use_phi_filter=False,
            enabled_tool_names=["query_documents"],
            documents_doc_key="lab",
        )
        self.decider_provider = decider_provider


def _principal():
    return Principal(
        user_id="user-1",
        sub="user-1",
        scopes=["chat.send"],
        patient_id=None,
        raw={},
        raw_token="token",
    )


def test_forces_prefetch_when_decider_skips_and_no_recent_evidence():
    db = _session()
    docs = StubDocumentsTools()
    agent = TestReasoningAgent(
        provider=MainProvider(),
        decider_provider=DeciderNoRetrievalProvider(),
        documents_tools=docs,
    )

    agent.chat(
        db=db,
        channel="bayleaf_app",
        user_message="Quais sao os valores normais do colesterol?",
        external_conversation_id="conv-1",
        principal=_principal(),
        lang="pt-BR",
        agent_slug="labcopilot",
    )

    assert len(docs.calls) == 1
    assert docs.calls[0]["query"] == "Quais sao os valores normais do colesterol?"


def test_reuses_recent_evidence_without_prefetch():
    db = _session()
    docs = StubDocumentsTools()
    agent = TestReasoningAgent(
        provider=MainProvider(),
        decider_provider=DeciderNoRetrievalProvider(),
        documents_tools=docs,
    )
    principal = _principal()

    conv = agent._get_or_create_conversation(
        db,
        "conv-2",
        principal.user_id,
        "bayleaf_app",
        agent_slug="labcopilot",
        group_id=None,
    )
    db.add(
        Message(
            conversation_id=conv.id,
            role=Role.tool,
            content="",
            redacted_content="",
            tool_name="query_documents",
            tool_result={
                "chunks": [
                    {
                        "document_uuid": "doc-1",
                        "name": "Clinical Book",
                        "text_chunk": "O colesterol nao vem exclusivamente da alimentacao e tambem e produzido pelo organismo.",
                    }
                ]
            },
        )
    )
    db.commit()

    agent.chat(
        db=db,
        channel="bayleaf_app",
        user_message="O colesterol vem exclusivamente da alimentacao?",
        external_conversation_id="conv-2",
        principal=principal,
        lang="pt-BR",
        agent_slug="labcopilot",
    )

    assert len(docs.calls) == 0

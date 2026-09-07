from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bayleaf_agents.agents.base_agent import BaseAgent
from bayleaf_agents.auth.deps import Principal
from bayleaf_agents.llm.base import LLMProvider
from bayleaf_agents.models import Base, Message, Role
from bayleaf_agents.tools.bayleaf import BayleafClient


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _principal():
    return Principal(
        user_id="user-1",
        sub="user-1",
        scopes=["chat.send"],
        patient_id=None,
        raw={},
        raw_token="token",
    )


class MainProvider(LLMProvider):
    name = "main-provider"

    def chat(self, messages, tools):
        return {"reply": "ok", "tool_calls": []}


class ToolCallingProvider(LLMProvider):
    name = "tool-calling-provider"

    def chat(self, messages, tools):
        if tools:
            return {
                "reply": "",
                "tool_calls": [
                    {"id": "call_1", "name": "query_documents", "args": {"query": "headache"}}
                ],
            }
        return {"reply": "ok", "tool_calls": []}


class DeciderNoRetrievalProvider(LLMProvider):
    name = "decider-no-retrieval"

    def chat(self, messages, tools):
        return {
            "reply": '{"needs_retrieval": false, "candidate_document_ids": [], "reason": "covered", "confidence": 0.6}',
            "tool_calls": [],
        }


class DocumentsToolsWithCatalog:
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
                    "text_chunk": "O colesterol tambem e produzido pelo organismo e nao vem so da alimentacao.",
                }
            ],
            "trace": {"trace_id": "prefetch_test_1"},
        }


class DocumentsToolsNoCatalog:
    def __init__(self):
        self.calls = []

    def query_documents(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "query": kwargs.get("query"),
            "chunks": [
                {
                    "document_uuid": "doc-1",
                    "name": "Clinical Book",
                    "chunk_index": 0,
                    "score": 0.8,
                    "text_chunk": "Hydration and rest are recommended.",
                }
            ],
            "trace": {"trace_id": "tool_call_test_1"},
        }


def test_base_agent_reuses_recent_evidence_when_decider_skips():
    db = _session()
    docs = DocumentsToolsWithCatalog()
    agent = BaseAgent(
        name="test-agent",
        objective="test objective",
        provider=MainProvider(),
        bayleaf=BayleafClient("http://example.test"),
        documents_tools=docs,
        decider_provider=DeciderNoRetrievalProvider(),
        use_phi_filter=False,
    )
    principal = _principal()

    conv = agent._get_or_create_conversation(
        db,
        "conv-1",
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
                        "text_chunk": "O colesterol tambem e produzido pelo organismo e nao vem so da alimentacao.",
                    }
                ]
            },
        )
    )
    db.commit()

    agent._process_chat(
        db=db,
        channel="bayleaf_app",
        user_message="O colesterol vem da alimentacao ou do organismo?",
        conversation_id=conv.id,
        principal=principal,
        lang="pt-BR",
        agent_slug="labcopilot",
    )

    assert len(docs.calls) == 0


def test_base_agent_forces_prefetch_when_decider_skips_and_no_recent_evidence():
    db = _session()
    docs = DocumentsToolsWithCatalog()
    agent = BaseAgent(
        name="test-agent",
        objective="test objective",
        provider=MainProvider(),
        bayleaf=BayleafClient("http://example.test"),
        documents_tools=docs,
        decider_provider=DeciderNoRetrievalProvider(),
        use_phi_filter=False,
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

    agent._process_chat(
        db=db,
        channel="bayleaf_app",
        user_message="Quais exames ajudam a investigar fadiga?",
        conversation_id=conv.id,
        principal=principal,
        lang="pt-BR",
        agent_slug="labcopilot",
    )

    assert len(docs.calls) == 1
    assert docs.calls[0]["top_k"] == 5
    assert docs.calls[0]["document_uuids"] is None


def test_base_agent_skips_decider_when_documents_catalog_not_available():
    db = _session()
    docs = DocumentsToolsNoCatalog()
    agent = BaseAgent(
        name="test-agent",
        objective="test objective",
        provider=ToolCallingProvider(),
        bayleaf=BayleafClient("http://example.test"),
        documents_tools=docs,
        decider_provider=DeciderNoRetrievalProvider(),
        use_phi_filter=False,
    )
    principal = _principal()

    conv = agent._get_or_create_conversation(
        db,
        "conv-3",
        principal.user_id,
        "bayleaf_app",
        agent_slug="labcopilot",
        group_id=None,
    )

    result = agent._process_chat(
        db=db,
        channel="bayleaf_app",
        user_message="I have a headache.",
        conversation_id=conv.id,
        principal=principal,
        lang="en-US",
        agent_slug="labcopilot",
    )

    assert result["used_tools"] == ["query_documents"]
    assert len(docs.calls) == 1

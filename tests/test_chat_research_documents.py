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


class QueryDocumentsProvider(LLMProvider):
    name = "query-documents-provider"

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        system = (messages[0]["content"] if messages else "") if isinstance(messages, list) else ""
        if isinstance(system, str) and "CitationExtractor" in system:
            return {
                "reply": (
                    '{"citations":[{"id":"c1","document_uuid":"doc-1","document_name":"Clinical Guide",'
                    '"chunk_ref":"doc-1#0","evidence_text":"Hydration and rest are recommended in mild headache cases."}]}'
                ),
                "tool_calls": [],
            }
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
                {
                    "document_uuid": "doc-1",
                    "name": "Clinical Guide",
                    "chunk_index": 0,
                    "score": 0.95,
                    "text_chunk": "Hydration and rest are recommended in mild headache cases.",
                },
                {
                    "document_uuid": "doc-1",
                    "name": "Clinical Guide",
                    "chunk_index": 1,
                    "score": 0.90,
                    "text_chunk": "Seek emergency care if severe neurological symptoms appear.",
                },
                {
                    "document_uuid": "doc-2",
                    "name": "Medication Reference",
                    "chunk_index": 0,
                    "score": 0.75,
                    "text_chunk": "Analgesic options include acetaminophen for eligible adults.",
                },
                {"document_uuid": "doc-3", "name": "", "chunk_index": 0, "score": 0.7, "text_chunk": "ignored"},
            ],
            "trace": {"trace_id": "retr_demo"},
        }


def test_chat_returns_cited_and_retrieved_documents():
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

    assert result["retrieved_documents"] == [
        {"name": "Clinical Guide", "uuid": "doc-1"},
        {"name": "Medication Reference", "uuid": "doc-2"},
    ]
    assert result["cited_documents"] == [{"name": "Clinical Guide", "uuid": "doc-1"}]
    assert result["citations"] == [
        {
            "id": "c1",
            "document_uuid": "doc-1",
            "document_name": "Clinical Guide",
            "chunk_ref": "doc-1#0",
            "evidence_text": "Hydration and rest are recommended in mild headache cases.",
            "retrieval_score": 0.95,
        }
    ]
    saved_assistant_message = (
        db.query(Message)
        .filter(Message.role == Role.assistant)
        .order_by(Message.created_at.desc())
        .first()
    )
    assert saved_assistant_message is not None
    assert saved_assistant_message.cited_documents == [{"name": "Clinical Guide", "uuid": "doc-1"}]
    assert saved_assistant_message.citations == [
        {
            "id": "c1",
            "document_uuid": "doc-1",
            "document_name": "Clinical Guide",
            "chunk_ref": "doc-1#0",
            "evidence_text": "Hydration and rest are recommended in mild headache cases.",
            "retrieval_score": 0.95,
        }
    ]

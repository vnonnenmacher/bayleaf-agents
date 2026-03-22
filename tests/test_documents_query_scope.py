from bayleaf_agents.tools.documents import DocumentsToolset


class StubDocumentsService:
    default_model = "model-default"

    def __init__(self, scoped_uuids):
        self.scoped_uuids = list(scoped_uuids)
        self.query_calls = []

    def document_uuids_for_doc_key(self, *, doc_key, principal):
        _ = (doc_key, principal)
        return list(self.scoped_uuids)

    def query_documents(self, **kwargs):
        self.query_calls.append(kwargs)
        return {
            "query": kwargs.get("query"),
            "top_k": kwargs.get("top_k"),
            "model_used": kwargs.get("model_used") or self.default_model,
            "chunks": [],
            "trace": {"query_filter": "captured"},
        }


def test_doc_key_mode_uses_only_scoped_uuids():
    service = StubDocumentsService(scoped_uuids=["doc-1"])
    tools = DocumentsToolset(service)

    tools.query_documents(
        query="hematologia",
        doc_key="lab",
        principal=None,
        document_uuid="doc-out-of-scope",
        document_uuids=["doc-out-of-scope"],
    )

    assert len(service.query_calls) == 1
    assert service.query_calls[0]["document_uuid"] is None
    assert service.query_calls[0]["document_uuids"] == ["doc-1"]


def test_doc_key_mode_returns_empty_when_scope_is_empty():
    service = StubDocumentsService(scoped_uuids=[])
    tools = DocumentsToolset(service)

    out = tools.query_documents(query="hematologia", doc_key="lab", principal=None)

    assert len(service.query_calls) == 0
    assert out["chunks"] == []
    assert out["trace"]["scope_mode"] == "doc_key_strict"
    assert out["trace"]["scope_reason"] == "doc_key_no_documents"


def test_no_doc_key_keeps_original_filters():
    service = StubDocumentsService(scoped_uuids=["doc-1"])
    tools = DocumentsToolset(service)

    tools.query_documents(
        query="hematologia",
        doc_key=None,
        principal=None,
        document_uuid="doc-x",
        document_uuids=["doc-y"],
    )

    assert len(service.query_calls) == 1
    assert service.query_calls[0]["document_uuid"] == "doc-x"
    assert service.query_calls[0]["document_uuids"] == ["doc-y"]

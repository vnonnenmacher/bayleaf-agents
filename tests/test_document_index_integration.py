import types

from bayleaf_agents.auth.deps import Principal
from bayleaf_agents.services.qdrant_documents import QdrantDocumentsService
from bayleaf_agents.tools.bayleaf import BayleafClient


def _principal() -> Principal:
    return Principal(
        user_id="user-1",
        sub="user-1",
        scopes=["chat.send"],
        patient_id=None,
        raw={},
        raw_token="token",
    )


def _service(*, bayleaf):
    return QdrantDocumentsService(
        base_url="http://qdrant.test",
        collection_prefix="documents",
        distance="Cosine",
        timeout=1,
        bayleaf=bayleaf,
        allowed_models=["sentence-transformers/all-MiniLM-L6-v2"],
        default_model="sentence-transformers/all-MiniLM-L6-v2",
    )


def test_bayleaf_document_download_url_uses_document_uuid_path(monkeypatch):
    client = BayleafClient("http://bayleaf.test")
    captured = {}

    def fake_get(self, path, params=None, principal=None, *, use_auth=True, bearer_token=None):
        _ = (params, principal, use_auth, bearer_token)
        captured["path"] = path
        return {"ok": True}

    monkeypatch.setattr(client, "_get", types.MethodType(fake_get, client))

    out = client.document_download_url(document_uuid="doc-123", principal=_principal())

    assert out == {"ok": True}
    assert captured["path"] == "/api/documents/doc-123/download-url/"


def test_service_index_document_calls_new_bayleaf_method(monkeypatch):
    class StubBayleaf:
        def __init__(self):
            self.called_with = None

        def document_download_url(self, *, document_uuid, principal):
            self.called_with = {"document_uuid": document_uuid, "principal": principal}
            return {"download_url": "https://files.test/doc.pdf"}

    bayleaf = StubBayleaf()
    service = _service(bayleaf=bayleaf)

    monkeypatch.setattr(service, "_download_file", lambda url: (b"file-content", "doc.pdf", "application/pdf"))
    monkeypatch.setattr(service, "_extract_text", lambda content, filename, mime: ("hello world", "indexed"))
    monkeypatch.setattr(service, "_index_payload", lambda **kwargs: kwargs)

    principal = _principal()
    out = service.index_document(document_uuid="doc-abc", principal=principal)

    assert bayleaf.called_with == {"document_uuid": "doc-abc", "principal": principal}
    assert out["document_uuid"] == "doc-abc"
    assert out["source_type"] == "bayleaf"
    assert out["bayleaf_document_uuid"] == "doc-abc"


def test_reindex_bayleaf_document_accepts_legacy_payload_key(monkeypatch):
    class StubBayleaf:
        pass

    service = _service(bayleaf=StubBayleaf())
    principal = _principal()

    monkeypatch.setattr(
        service,
        "_find_latest_document_points",
        lambda document_uuid: (
            "sentence-transformers/all-MiniLM-L6-v2",
            [
                {
                    "payload": {
                        "source_type": "bayleaf",
                        "bayleaf_document_version_uuid": "legacy-doc-uuid",
                    }
                }
            ],
        ),
    )

    captured = {}

    def fake_index_document(*, document_uuid, principal, model_used=None):
        captured["document_uuid"] = document_uuid
        captured["principal"] = principal
        captured["model_used"] = model_used
        return {"uuid": document_uuid}

    monkeypatch.setattr(service, "index_document", fake_index_document)

    out = service.reindex_document(document_uuid="ignored", principal=principal)

    assert out == {"uuid": "legacy-doc-uuid"}
    assert captured["document_uuid"] == "legacy-doc-uuid"
    assert captured["principal"] == principal


def test_extract_document_uuids_accepts_document_id_when_uuid():
    service = _service(bayleaf=object())
    document_uuid = "d616ccac-4f2c-4cc2-adcb-4c804f8a8d88"

    out = service._extract_document_uuids(
        {
            "id": document_uuid,
            "org": "b272db0e-76df-4e13-94e9-2cb01c68978d",
            "doc_key": "lab.sop.hemato",
            "name": "POP Hemato",
            "reference": "minio://bucket/path",
            "created_by": "ffafcb80-4191-43e6-aaa2-c1a197a49aa5",
        }
    )

    assert out == {document_uuid}


def test_extract_document_uuids_rejects_document_id_when_not_uuid():
    service = _service(bayleaf=object())

    out = service._extract_document_uuids(
        {
            "id": "doc-123",
            "doc_key": "lab.sop.hemato",
            "reference": "minio://bucket/path",
        }
    )

    assert out == set()

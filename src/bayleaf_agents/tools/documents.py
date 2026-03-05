from typing import Any, Dict, Optional

import structlog

from ..auth.deps import Principal
from ..services.qdrant_documents import QdrantDocumentsService


class DocumentsToolset:
    def __init__(self, documents_service: QdrantDocumentsService):
        self.documents_service = documents_service
        self.log = structlog.get_logger("documents_tools")

    def query_documents(
        self,
        *,
        query: str,
        top_k: int = 5,
        model_used: Optional[str] = None,
        document_uuid: Optional[str] = None,
        document_uuids: Optional[list[str]] = None,
        source_type: Optional[str] = None,
        is_bayleaf: Optional[bool] = None,
        doc_key: Optional[str] = None,
        principal: Optional[Principal] = None,
    ) -> Dict[str, Any]:
        resolved_document_uuids = list(document_uuids or [])
        if doc_key:
            scoped_uuids = self.documents_service.document_uuids_for_doc_key(
                doc_key=doc_key,
                principal=principal,
            )
            if document_uuid:
                if document_uuid in scoped_uuids:
                    resolved_document_uuids = [document_uuid]
                else:
                    resolved_document_uuids = []
            else:
                if resolved_document_uuids:
                    allowed = set(scoped_uuids)
                    resolved_document_uuids = [doc_id for doc_id in resolved_document_uuids if doc_id in allowed]
                else:
                    resolved_document_uuids = scoped_uuids

        resolved_document_uuid = document_uuid
        if resolved_document_uuids:
            resolved_document_uuid = None

        return self.documents_service.query_documents(
            query=query,
            top_k=top_k,
            model_used=model_used,
            document_uuid=resolved_document_uuid,
            document_uuids=resolved_document_uuids or None,
            source_type=source_type,
            is_bayleaf=is_bayleaf,
        )

    def documents_available(
        self,
        *,
        doc_key: Optional[str] = None,
        principal: Optional[Principal] = None,
    ) -> list[dict]:
        if not doc_key:
            docs = self.documents_service.documents_available()
            self.log.info(
                "documents_available_scope",
                doc_key=None,
                principal_user_id=(principal.user_id if principal else None),
                scoped_uuids_count=None,
                global_docs_count=len(docs),
                returned_docs_count=len(docs),
            )
            return docs
        global_docs = self.documents_service.documents_available()
        scoped_uuids = set(
            self.documents_service.document_uuids_for_doc_key(
                doc_key=doc_key,
                principal=principal,
            )
        )
        if not scoped_uuids:
            self.log.info(
                "documents_available_scope",
                doc_key=doc_key,
                principal_user_id=(principal.user_id if principal else None),
                scoped_uuids_count=0,
                global_docs_count=len(global_docs),
                returned_docs_count=0,
            )
            return []
        docs = [
            d for d in global_docs
            if str(d.get("uuid") or "") in scoped_uuids
        ]
        self.log.info(
            "documents_available_scope",
            doc_key=doc_key,
            principal_user_id=(principal.user_id if principal else None),
            scoped_uuids_count=len(scoped_uuids),
            global_docs_count=len(global_docs),
            returned_docs_count=len(docs),
        )
        return docs


def query_tool_schemas() -> list[dict]:
    return [
        {
            "name": "query_documents",
            "description": "Query indexed document chunks in Qdrant and return scored matches with retrieval trace metadata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
                    "model_used": {"type": "string"},
                    "document_uuid": {"type": "string"},
                    "document_uuids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "source_type": {"type": "string", "enum": ["bayleaf", "uploaded"]},
                    "is_bayleaf": {"type": "boolean"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        }
    ]

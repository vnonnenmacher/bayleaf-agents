import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import structlog

from ..auth.deps import Principal
from ..services.qdrant_documents import QdrantDocumentsService


class DocumentsToolset:
    def __init__(self, documents_service: QdrantDocumentsService):
        self.documents_service = documents_service
        self.log = structlog.get_logger("documents_tools")

    def _empty_scoped_result(
        self,
        *,
        query: str,
        top_k: int,
        model_used: Optional[str],
        reason: str,
    ) -> Dict[str, Any]:
        resolved_model = str(model_used or getattr(self.documents_service, "default_model", ""))
        return {
            "query": query,
            "top_k": top_k,
            "model_used": resolved_model,
            "chunks": [],
            "trace": {
                "trace_id": f"retr_{uuid.uuid4().hex[:12]}",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "collection": None,
                "model_used": resolved_model,
                "query_filter": {"should": []},
                "requested_top_k": top_k,
                "returned_chunks": 0,
                "scope_mode": "doc_key_strict",
                "scope_reason": reason,
            },
        }

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
        if doc_key:
            scoped_uuids = self.documents_service.document_uuids_for_doc_key(
                doc_key=doc_key,
                principal=principal,
            )
            self.log.info(
                "documents_query_scope_debug",
                doc_key=doc_key,
                principal_user_id=(principal.user_id if principal else None),
                scoped_uuids_count=len(scoped_uuids),
                scoped_uuids=scoped_uuids[:20],
            )
            if not scoped_uuids:
                return self._empty_scoped_result(
                    query=query,
                    top_k=top_k,
                    model_used=model_used,
                    reason="doc_key_no_documents",
                )

            scoped_set = {str(doc_id).strip() for doc_id in scoped_uuids if str(doc_id).strip()}
            effective_document_uuid: Optional[str] = None
            effective_document_uuids: Optional[list[str]] = None

            if document_uuid:
                requested_uuid = str(document_uuid).strip()
                if requested_uuid not in scoped_set:
                    return self._empty_scoped_result(
                        query=query,
                        top_k=top_k,
                        model_used=model_used,
                        reason="doc_key_document_uuid_out_of_scope",
                    )
                effective_document_uuid = requested_uuid
            elif document_uuids:
                requested_uuids = [str(doc_id).strip() for doc_id in document_uuids if str(doc_id).strip()]
                filtered = [doc_id for doc_id in requested_uuids if doc_id in scoped_set]
                if not filtered:
                    return self._empty_scoped_result(
                        query=query,
                        top_k=top_k,
                        model_used=model_used,
                        reason="doc_key_document_uuids_out_of_scope",
                    )
                # Keep order from caller while removing duplicates.
                seen: set[str] = set()
                effective_document_uuids = []
                for doc_id in filtered:
                    if doc_id in seen:
                        continue
                    seen.add(doc_id)
                    effective_document_uuids.append(doc_id)
            else:
                effective_document_uuids = [doc_id for doc_id in scoped_uuids if str(doc_id).strip()]

            return self.documents_service.query_documents(
                query=query,
                top_k=top_k,
                model_used=model_used,
                document_uuid=effective_document_uuid,
                document_uuids=effective_document_uuids,
                source_type=source_type,
                is_bayleaf=is_bayleaf,
            )

        return self.documents_service.query_documents(
            query=query,
            top_k=top_k,
            model_used=model_used,
            document_uuid=document_uuid,
            document_uuids=document_uuids,
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
        global_uuids = [
            str(d.get("uuid") or "")
            for d in global_docs
            if str(d.get("uuid") or "")
        ]
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
            self.log.info(
                "documents_available_scope_debug",
                doc_key=doc_key,
                principal_user_id=(principal.user_id if principal else None),
                global_uuids_sample=global_uuids[:20],
                scoped_uuids=[],
                returned_uuids=[],
            )
            return []
        docs = [
            d for d in global_docs
            if str(d.get("uuid") or "") in scoped_uuids
        ]
        returned_uuids = [
            str(d.get("uuid") or "")
            for d in docs
            if str(d.get("uuid") or "")
        ]
        self.log.info(
            "documents_available_scope",
            doc_key=doc_key,
            principal_user_id=(principal.user_id if principal else None),
            scoped_uuids_count=len(scoped_uuids),
            global_docs_count=len(global_docs),
            returned_docs_count=len(docs),
        )
        self.log.info(
            "documents_available_scope_debug",
            doc_key=doc_key,
            principal_user_id=(principal.user_id if principal else None),
            global_uuids_sample=global_uuids[:20],
            scoped_uuids=sorted(scoped_uuids)[:20],
            returned_uuids=returned_uuids[:20],
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

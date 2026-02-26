from typing import Any, Dict, Optional

from ..services.qdrant_documents import QdrantDocumentsService


class DocumentsToolset:
    def __init__(self, documents_service: QdrantDocumentsService):
        self.documents_service = documents_service

    def query_documents(
        self,
        *,
        query: str,
        top_k: int = 5,
        model_used: Optional[str] = None,
        document_uuid: Optional[str] = None,
        source_type: Optional[str] = None,
        is_bayleaf: Optional[bool] = None,
    ) -> Dict[str, Any]:
        return self.documents_service.query_documents(
            query=query,
            top_k=top_k,
            model_used=model_used,
            document_uuid=document_uuid,
            source_type=source_type,
            is_bayleaf=is_bayleaf,
        )


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
                    "source_type": {"type": "string", "enum": ["bayleaf", "uploaded"]},
                    "is_bayleaf": {"type": "boolean"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        }
    ]

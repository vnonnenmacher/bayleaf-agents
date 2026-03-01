import json
import re
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ...auth.deps import Principal
from ...llm.base import LLMProvider
from ...models import Message
from ...tools.documents import DocumentsToolset


class DocumentDeciderAgent:
    def __init__(self, provider: LLMProvider, documents_tools: DocumentsToolset):
        self.provider = provider
        self.documents_tools = documents_tools

    def _history_text(self, db: Session, conversation_id: str, limit: int = 12) -> List[str]:
        msgs = (
            db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
            .all()
        )
        lines: List[str] = []
        for m in reversed(msgs):
            content = (m.redacted_content or m.content or "").strip()
            if content:
                lines.append(f"{m.role.value}: {content[:500]}")
        return lines

    def _parse_json(self, raw: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(raw)
        except Exception:
            pass
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except Exception:
            return None

    def decide_documents(
        self,
        *,
        db: Session,
        conversation_id: Optional[str],
        user_message: str,
        lang: str = "pt-BR",
        principal: Optional[Principal] = None,
        doc_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        available = self.documents_tools.documents_available(
            doc_key=doc_key,
            principal=principal,
        )
        docs_catalog = [
            {
                "uuid": doc.get("uuid"),
                "name": doc.get("name"),
                "source_type": doc.get("source_type"),
                "is_bayleaf": doc.get("is_bayleaf"),
                "status": doc.get("status"),
                "description": doc.get("description"),
                "indexed_at": doc.get("indexed_at"),
            }
            for doc in available
        ]

        system = (
            "You are DocumentDeciderAgent. Decide if retrieval is needed and which document UUIDs are most relevant.\n"
            "Return ONLY strict JSON with keys: needs_retrieval (boolean), candidate_document_ids (array of UUID strings), "
            "reason (string), confidence (number 0..1).\n"
            "If retrieval is not needed, return an empty candidate_document_ids."
        )
        history_lines = self._history_text(db, conversation_id=conversation_id) if conversation_id else []
        history = "\n".join(history_lines)
        prompt = (
            f"Language: {lang}\n"
            f"Conversation history:\n{history or '(empty)'}\n\n"
            f"Current user message:\n{user_message}\n\n"
            f"Available documents catalog:\n{json.dumps(docs_catalog, ensure_ascii=False)}"
        )

        out = self.provider.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            tools=[],
        )
        parsed = self._parse_json(out.get("reply") or "")
        if not isinstance(parsed, dict):
            return {
                "needs_retrieval": False,
                "candidate_document_ids": [],
                "reason": "decider_parse_failed",
                "confidence": 0.0,
                "available_documents_count": len(docs_catalog),
            }

        ids = parsed.get("candidate_document_ids") or []
        if not isinstance(ids, list):
            ids = []
        valid_ids = {d.get("uuid") for d in docs_catalog if d.get("uuid")}
        ids = [str(x) for x in ids if str(x) in valid_ids]

        needs_retrieval = bool(parsed.get("needs_retrieval")) and bool(ids)
        return {
            "needs_retrieval": needs_retrieval,
            "candidate_document_ids": ids,
            "reason": str(parsed.get("reason") or ""),
            "confidence": float(parsed.get("confidence") or 0.0),
            "available_documents_count": len(docs_catalog),
        }

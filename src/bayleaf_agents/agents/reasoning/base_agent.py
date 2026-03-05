import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..base_agent import BaseAgent
from ...auth.deps import Principal
from ...models import Message, Role
from .document_decider_agent import DocumentDeciderAgent


class ReasoningBaseAgent(BaseAgent):
    """
    Base agent for routing/reasoning-first assistants.
    Keeps compatibility with the existing BaseAgent chat lifecycle while
    exposing lightweight helpers for explicit routing decisions.
    """

    def build_route_context(
        self,
        *,
        user_message: str,
        state: Optional[Dict[str, Any]] = None,
        channel: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "user_message": user_message,
            "channel": channel,
            "state": state or {},
        }

    def default_route(self) -> Dict[str, Any]:
        return {
            "route": "no_retrieval",
            "reason": "no_routing_logic_implemented",
            "confidence": 0.0,
        }

    def _build_decider(self) -> Optional[DocumentDeciderAgent]:
        if not self.documents_tools:
            return None
        provider = getattr(self, "decider_provider", None) or self.provider
        return DocumentDeciderAgent(provider=provider, documents_tools=self.documents_tools)

    def _tokenize(self, text: str) -> List[str]:
        return [t for t in re.findall(r"[a-zA-ZÀ-ÿ0-9]+", (text or "").lower()) if len(t) >= 4]

    def _latest_query_documents_message(self, db: Session, conversation_id: Optional[str]) -> Optional[Message]:
        if not conversation_id:
            return None
        return (
            db.query(Message)
            .filter(
                Message.conversation_id == conversation_id,
                Message.role == Role.tool,
                Message.tool_name == "query_documents",
            )
            .order_by(Message.created_at.desc())
            .first()
        )

    def _latest_query_documents_chunks(self, db: Session, conversation_id: Optional[str]) -> List[Dict[str, Any]]:
        msg = self._latest_query_documents_message(db, conversation_id)
        if not msg or not isinstance(msg.tool_result, dict):
            return []
        chunks = msg.tool_result.get("chunks")
        if not isinstance(chunks, list):
            return []
        return [c for c in chunks if isinstance(c, dict)]

    def _has_explicit_recent_evidence(
        self,
        *,
        user_message: str,
        chunks: List[Dict[str, Any]],
    ) -> Tuple[bool, int]:
        if not chunks:
            return False, 0
        user_terms = set(self._tokenize(user_message))
        if not user_terms:
            return False, 0
        matched_chunks = 0
        for chunk in chunks:
            text = str(chunk.get("text_chunk") or "")
            chunk_terms = set(self._tokenize(text))
            overlap = user_terms.intersection(chunk_terms)
            if len(overlap) >= 3:
                matched_chunks += 1
        return matched_chunks > 0, matched_chunks

    def _is_high_risk_question(self, user_message: str) -> bool:
        text = (user_message or "").lower()
        patterns = [
            r"\bvalor(?:es)?\b",
            r"\bfaixa(?:s)?\b",
            r"\brefer[êe]ncia\b",
            r"\blimite(?:s)?\b",
            r"\bnormal(?:es)?\b",
            r"\bprocedimento\b",
            r"\bcomo (?:fazer|coletar|preparar)\b",
            r"\bcut[- ]?off\b",
        ]
        return any(re.search(p, text) for p in patterns)

    def _user_turns_since_message(self, db: Session, conversation_id: Optional[str], message_ts: Optional[datetime]) -> int:
        if not conversation_id or not message_ts:
            return 999
        return int(
            db.query(Message)
            .filter(
                Message.conversation_id == conversation_id,
                Message.role == Role.user,
                Message.created_at > message_ts,
            )
            .count()
        )

    def _has_query_shift(self, *, user_message: str, chunks: List[Dict[str, Any]]) -> bool:
        # If user adds qualifiers/constraints that are absent in previously retrieved chunks,
        # force retrieval even with generic token overlap.
        shift_lexicon = {
            "gestante", "gravida", "criança", "crianca", "pediatrico", "idoso",
            "jejum", "posprandial", "método", "metodo", "técnica", "tecnica",
            "ldl", "hdl", "vldl", "triglicerides", "triglicérides", "nao-hdl",
            "diabet", "renal", "hepatic", "diretriz", "guideline",
        }
        user_tokens = set(self._tokenize(user_message))
        user_shift_tokens = {t for t in user_tokens if t in shift_lexicon}
        if not user_shift_tokens:
            return False
        chunk_tokens: set[str] = set()
        for chunk in chunks:
            if isinstance(chunk, dict):
                chunk_tokens.update(self._tokenize(str(chunk.get("text_chunk") or "")))
        return any(token not in chunk_tokens for token in user_shift_tokens)

    def _research_documents_from_prefetch(self, prefetch_result: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
        if not isinstance(prefetch_result, dict):
            return []
        chunks = prefetch_result.get("chunks")
        if not isinstance(chunks, list):
            return []
        out: List[Dict[str, str]] = []
        seen: set[str] = set()
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            doc_uuid = str(chunk.get("document_uuid") or "").strip()
            name = str(chunk.get("name") or "").strip()
            if not doc_uuid or not name or doc_uuid in seen:
                continue
            seen.add(doc_uuid)
            out.append({"name": name, "uuid": doc_uuid})
        return out

    def chat(
        self,
        db: Session,
        channel: str,
        user_message: str,
        external_conversation_id: Optional[str],
        *,
        principal: Optional[Principal] = None,
        lang: str = "en-US",
        agent_slug: Optional[str] = None,
        group_id: Optional[str] = None,
        group_context: Optional[Dict[str, Any]] = None,
        forced_document_ids: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        conv_id: Optional[str] = None
        if external_conversation_id:
            conv = self._get_or_create_conversation(
                db,
                external_conversation_id,
                principal.user_id,
                channel,
                agent_slug=agent_slug,
                group_id=group_id,
            )
            conv_id = conv.id
        decider = self._build_decider()
        route_trace: Dict[str, Any] = {"decider": None}
        candidate_ids: list[str] = []
        forced_retrieval_reason: Optional[str] = None
        latest_chunks = self._latest_query_documents_chunks(db, conv_id)
        latest_tool_msg = self._latest_query_documents_message(db, conv_id)
        has_recent_evidence, evidence_hits = self._has_explicit_recent_evidence(
            user_message=user_message,
            chunks=latest_chunks,
        )
        high_risk_question = self._is_high_risk_question(user_message)
        query_shift = self._has_query_shift(user_message=user_message, chunks=latest_chunks)
        turns_since_last_retrieval = self._user_turns_since_message(
            db,
            conv_id,
            (latest_tool_msg.created_at if latest_tool_msg else None),
        )
        should_retrieve = False
        prefetch_result: Optional[Dict[str, Any]] = None
        routing_mode = "no_decider"

        if decider:
            decision = decider.decide_documents(
                db=db,
                conversation_id=conv_id,
                user_message=user_message,
                lang=lang,
                principal=principal,
                doc_key=self.documents_doc_key,
            )
            route_trace["decider"] = decision
            available_count = int(decision.get("available_documents_count") or 0)
            if decision.get("needs_retrieval"):
                candidate_ids = list(decision.get("candidate_document_ids") or [])
                should_retrieve = True
                routing_mode = "decider_retrieval"
            elif high_risk_question and available_count > 0:
                should_retrieve = True
                routing_mode = "forced_high_risk"
                forced_retrieval_reason = "high_risk_question"
            elif query_shift and available_count > 0:
                should_retrieve = True
                routing_mode = "forced_query_shift"
                forced_retrieval_reason = "query_shift_detected"
            elif turns_since_last_retrieval > 1 and available_count > 0:
                should_retrieve = True
                routing_mode = "forced_stale_reuse_window"
                forced_retrieval_reason = "reuse_window_exceeded"
            elif available_count > 0 and not has_recent_evidence:
                # Deterministic fallback:
                # if decider skips retrieval but we have docs and no explicit evidence
                # in recent retrieved chunks, force a new retrieval.
                should_retrieve = True
                routing_mode = "forced_fallback_no_evidence"
                forced_retrieval_reason = "no_explicit_recent_evidence"
            elif has_recent_evidence:
                routing_mode = "reuse_recent_evidence"
            else:
                routing_mode = "skip_no_catalog"

            route_trace["policy"] = {
                "has_recent_evidence": has_recent_evidence,
                "evidence_hits": evidence_hits,
                "high_risk_question": high_risk_question,
                "query_shift": query_shift,
                "turns_since_last_retrieval": turns_since_last_retrieval,
                "should_retrieve": should_retrieve,
                "fallback_forced": bool(not decision.get("needs_retrieval") and should_retrieve),
                "routing_mode": routing_mode,
                "forced_retrieval_reason": forced_retrieval_reason,
            }
            try:
                self.log.info(
                    "retrieval_routing_decision",
                    decider=decision,
                    has_recent_evidence=has_recent_evidence,
                    evidence_hits=evidence_hits,
                    high_risk_question=high_risk_question,
                    query_shift=query_shift,
                    turns_since_last_retrieval=turns_since_last_retrieval,
                    should_retrieve=should_retrieve,
                    fallback_forced=bool(not decision.get("needs_retrieval") and should_retrieve),
                    routing_mode=routing_mode,
                    forced_retrieval_reason=forced_retrieval_reason,
                )
            except Exception:
                pass

            if forced_retrieval_reason:
                try:
                    self.log.info(
                        "reuse_guard_trigger",
                        reason=forced_retrieval_reason,
                        routing_mode=routing_mode,
                        query=user_message,
                    )
                except Exception:
                    pass

            if should_retrieve and self.documents_tools:
                prefetch_result = self.documents_tools.query_documents(
                    query=user_message,
                    top_k=5,
                    document_uuids=candidate_ids or None,
                    doc_key=self.documents_doc_key,
                    principal=principal,
                )
                route_trace["prefetch"] = {
                    "requested_query": user_message,
                    "candidate_document_ids": candidate_ids,
                    "returned_chunks": len((prefetch_result or {}).get("chunks") or []),
                    "trace": (prefetch_result or {}).get("trace"),
                }
                try:
                    self.log.info(
                        "retrieval_prefetch_done",
                        requested_query=user_message,
                        candidate_document_ids=candidate_ids,
                        returned_chunks=len((prefetch_result or {}).get("chunks") or []),
                        trace=(prefetch_result or {}).get("trace"),
                        routing_mode=routing_mode,
                    )
                except Exception:
                    pass

        effective_group_context = dict(group_context or {})
        if prefetch_result:
            chunks = (prefetch_result.get("chunks") or []) if isinstance(prefetch_result, dict) else []
            effective_group_context["retrieval_context"] = {
                "query": user_message,
                "chunks": [
                    {
                        "document_uuid": c.get("document_uuid"),
                        "name": c.get("name"),
                        "score": c.get("score"),
                        "text_chunk": str(c.get("text_chunk") or "")[:700],
                    }
                    for c in chunks[:5]
                    if isinstance(c, dict)
                ],
                "trace": (prefetch_result.get("trace") if isinstance(prefetch_result, dict) else None),
            }

        result = super().chat(
            db=db,
            channel=channel,
            user_message=user_message,
            external_conversation_id=external_conversation_id,
            principal=principal,
            lang=lang,
            candidate_document_ids=candidate_ids,
            document_route_trace=route_trace,
            agent_slug=agent_slug,
            group_id=group_id,
            group_context=effective_group_context or None,
            forced_document_ids=forced_document_ids,
        )
        prefetched_docs = self._research_documents_from_prefetch(prefetch_result)
        if prefetched_docs:
            existing = result.get("research_documents") or []
            seen = {str(d.get("uuid") or "") for d in existing if isinstance(d, dict)}
            for doc in prefetched_docs:
                if doc["uuid"] in seen:
                    continue
                existing.append(doc)
                seen.add(doc["uuid"])
            result["research_documents"] = existing
        return result

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from ..base_agent import BaseAgent
from ...auth.deps import Principal
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
    ) -> Dict[str, Any]:
        conv_id: Optional[str] = None
        if external_conversation_id:
            conv = self._get_or_create_conversation(
                db, external_conversation_id, principal.user_id, channel, agent_slug=agent_slug
            )
            conv_id = conv.id
        decider = self._build_decider()
        route_trace: Dict[str, Any] = {"decider": None}
        candidate_ids: list[str] = []
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
            if decision.get("needs_retrieval"):
                candidate_ids = list(decision.get("candidate_document_ids") or [])

        return super().chat(
            db=db,
            channel=channel,
            user_message=user_message,
            external_conversation_id=external_conversation_id,
            principal=principal,
            lang=lang,
            candidate_document_ids=candidate_ids,
            document_route_trace=route_trace,
            agent_slug=agent_slug,
        )

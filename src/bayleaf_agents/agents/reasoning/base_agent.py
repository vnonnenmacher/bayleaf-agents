from typing import Any, Dict, Optional

from ..base_agent import BaseAgent


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

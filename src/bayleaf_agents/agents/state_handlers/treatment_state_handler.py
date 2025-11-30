from typing import Any, Dict

from .base_state_handler import BaseStateHandler


class TreatmentStateHandler(BaseStateHandler):
    """
    Placeholder for treatment/medication state transitions.
    """

    def apply(self, tool_name: str, args: Dict[str, Any], result: Any, state: Dict[str, Any]) -> bool:
        # No treatment-specific state yet.
        return False

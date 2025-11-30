import structlog
from typing import Any, Dict


class BaseStateHandler:
    """
    Applies tool results to the conversational state.

    Subclasses override `apply` to mutate the provided state dictionary and
    return True when the state changed.
    """

    def __init__(self, log=None):
        self.log = log or structlog.get_logger("agent")

    def apply(self, tool_name: str, args: Dict[str, Any], result: Any, state: Dict[str, Any]) -> bool:
        """
        Apply a tool result; mutate `state` in-place. Return True if changed.
        """
        return False

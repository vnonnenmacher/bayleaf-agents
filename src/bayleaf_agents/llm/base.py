from typing import Any, Dict, List, TypedDict, Optional


class ToolSchema(TypedDict, total=False):
    name: str
    description: str
    parameters: Dict[str, Any]


class ToolCall(TypedDict, total=False):
    id: str
    name: str
    args: Dict[str, Any]


class ChatOutput(TypedDict, total=False):
    reply: Optional[str]
    tool_calls: List[ToolCall]


class LLMProvider:
    name: str = "base"

    def chat(self, messages: List[Dict[str, str]], tools: List[ToolSchema]) -> ChatOutput:
        raise NotImplementedError

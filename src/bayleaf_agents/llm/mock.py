from typing import Dict, List
from .base import LLMProvider, ChatOutput, ToolSchema


class MockProvider(LLMProvider):
    name = "mock"

    def chat(self, messages: List[Dict[str, str]], tools: List[ToolSchema]) -> ChatOutput:
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        wants_meds = any(k in user.lower() for k in ["remédio", "remedios", "medicação", "medication", "meds"])
        calls = []
        if wants_meds and any(t["name"] == "list_medications" for t in tools):
            calls.append({"id": "call_1", "name": "list_medications", "args": {}})
        reply = "Certo, vou verificar seus medicamentos." if wants_meds else \
                "Conte mais sobre seus sintomas (início, intensidade, gatilhos)."
        return {"reply": reply, "tool_calls": calls}

import json
from typing import Dict, List
from openai import OpenAI
from .base import LLMProvider, ChatOutput, ToolSchema


def _to_oai_tools(tools: List[ToolSchema]):
    return [{"type": "function", "function": t} for t in tools]


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.name = f"openai:{model}"

    def chat(self, messages: List[Dict[str, str]], tools: List[ToolSchema]) -> ChatOutput:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": _to_oai_tools(tools),
            "temperature": 0.2,
        }
        try:
            print(f"SENDING TO OPENAI: {json.dumps(payload, ensure_ascii=False)}")
        except Exception:
            print("SENDING TO OPENAI: <unserializable payload>")

        resp = self.client.chat.completions.create(**payload)
        msg = resp.choices[0].message
        out: ChatOutput = {"reply": msg.content or "", "tool_calls": []}
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = {}
                if tc.function.arguments:
                    try:
                        args = json.loads(tc.function.arguments)
                    except Exception:
                        args = {}
                out["tool_calls"].append({"id": tc.id, "name": tc.function.name, "args": args})
        return out

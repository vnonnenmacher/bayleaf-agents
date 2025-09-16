import uuid, time, structlog
from typing import Dict, Any, List
from ..config import settings
from ..llm.base import LLMProvider
from ..llm.mock import MockProvider
from ..tools.bayleaf import BayleafClient, tool_schemas

try:
    from ..llm.openai_provider import OpenAIProvider
except Exception:
    OpenAIProvider = None

log = structlog.get_logger("agent")


def get_provider() -> LLMProvider:
    if settings.LLM_PROVIDER == "openai" and OpenAIProvider:
        return OpenAIProvider(api_key=settings.OPENAI_API_KEY, model=settings.OPENAI_MODEL)
    return MockProvider()


PROVIDER = get_provider()
BAYLEAF = BayleafClient(settings.BAYLEAF_BASE_URL, settings.BAYLEAF_TOKEN)


def run_chat(patient_id: str, user_message: str) -> dict:
    trace = "chat_" + uuid.uuid4().hex[:12]
    t0 = time.time()

    system = (
        "Você é um assistente de saúde que fornece triagem e educação (NÃO diagnóstico). "
        "Use ferramentas quando necessário. Não inclua identificadores sensíveis."
    )
    messages = [
        {"role":"system","content": system},
        {"role":"user","content": user_message},
    ]

    tools = tool_schemas()
    out = PROVIDER.chat(messages, tools)

    used_tools: List[str] = []
    tool_results: List[dict] = []

    if out.get("tool_calls"):
        # Enforce scoping: never trust model-provided patient IDs
        messages.append({"role":"assistant","content":"","tool_calls": out["tool_calls"]})  # for OpenAI shape
        for tc in out["tool_calls"]:
            name = tc["name"]
            used_tools.append(name)
            if name == "patient_summary":
                result = BAYLEAF.patient_summary(patient_id)
            elif name == "list_medications":
                result = BAYLEAF.list_medications(patient_id)
            else:
                result = {"error": "unknown_tool"}
            tool_results.append({"name": name, "result": result})
            # Append tool result back
            messages.append({"role":"tool","tool_call_id": tc.get("id","tool"), "content": str(result)})

        # Ask provider for final response (providers usually ignore extra "tools" now)
        final = PROVIDER.chat(messages, tools=[])
        reply = final.get("reply") or out.get("reply") or "Certo."
    else:
        reply = out.get("reply") or "Certo."

    log.info("chat_done", trace_id=trace, tools=used_tools, ms=int((time.time()-t0)*1000))
    return {
        "reply": reply,
        "used_tools": used_tools,
        "trace_id": trace,
    }

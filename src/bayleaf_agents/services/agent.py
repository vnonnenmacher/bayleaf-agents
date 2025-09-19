import uuid, time, structlog
from typing import Dict, Any, List, Optional
from ..config import settings
from ..llm.base import LLMProvider
from ..llm.mock import MockProvider
from ..tools.bayleaf import BayleafClient, tool_schemas
from sqlalchemy.orm import Session
from ..models import Conversation, Message, Role


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
BAYLEAF = BayleafClient(settings.BAYLEAF_BASE_URL)


MAX_HISTORY_MSGS = 20  # simple cap; later replace with token-based trimming


def _get_or_create_conversation(db: Session, external_id: Optional[str], patient_id: str, channel: str) -> Conversation:
    if external_id:
        conv = db.query(Conversation).filter_by(external_id=external_id, patient_id=patient_id, channel=channel).first()
        if conv:
            return conv
    conv = Conversation(external_id=external_id, patient_id=patient_id, channel=channel)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def _load_history(db: Session, conv_id: str) -> list[dict]:
    q = (
        db.query(Message)
        .filter(Message.conversation_id == conv_id)
        .order_by(Message.created_at.asc())
        .limit(MAX_HISTORY_MSGS)
    )
    msgs = []
    for m in q.all():
        if m.role == Role.tool:
            msgs.append({"role":"tool","content": m.content, "tool_call_id": m.tool_name or "tool"})
        else:
            msgs.append({"role": m.role.value, "content": m.content})
    return msgs


def run_chat(db: Session, *, channel: str, patient_id: str, user_message: str, external_conversation_id: Optional[str]) -> dict:
    trace = "chat_" + uuid.uuid4().hex[:12]
    t0 = time.time()

    conv = _get_or_create_conversation(db, external_conversation_id, patient_id, channel)

    system = (
        "Você é um assistente de saúde que fornece triagem e educação (NÃO diagnóstico). "
        "Use ferramentas quando necessário. Não inclua identificadores sensíveis."
    )

    messages = [{"role":"system","content": system}]
    messages.extend(_load_history(db, conv.id))
    messages.append({"role":"user","content": user_message})

    # persist user message
    db.add(Message(conversation_id=conv.id, role=Role.user, content=user_message))
    db.commit()

    tools = tool_schemas()
    out = PROVIDER.chat(messages, tools)

    used_tools: List[str] = []
    if out.get("tool_calls"):
        # record assistant tool intent
        db.add(Message(conversation_id=conv.id, role=Role.assistant, content="", tool_name="__tool_calls__", tool_args={"calls": out["tool_calls"]}))
        db.commit()

        messages.append({"role":"assistant","content":"", "tool_calls": out["tool_calls"]})
        for tc in out["tool_calls"]:
            name = tc["name"]; used_tools.append(name)
            if name == "patient_summary":
                result = BAYLEAF.patient_summary(patient_id)
            elif name == "list_medications":
                result = BAYLEAF.list_medications(patient_id)
            else:
                result = {"error":"unknown_tool"}
            # persist tool result
            db.add(Message(conversation_id=conv.id, role=Role.tool, content=str(result), tool_name=name, tool_result=result))
            db.commit()

            messages.append({"role":"tool","tool_call_id": tc.get("id","tool"), "content": str(result)})

        final = PROVIDER.chat(messages, tools=[])
        reply = final.get("reply") or out.get("reply") or "Certo."
    else:
        reply = out.get("reply") or "Certo."

    # persist assistant reply
    db.add(Message(conversation_id=conv.id, role=Role.assistant, content=reply))
    db.commit()

    structlog.get_logger("agent").info("chat_done", trace_id=trace, tools=used_tools, ms=int((time.time()-t0)*1000))
    return {"reply": reply, "used_tools": used_tools, "trace_id": trace, "conversation_id": conv.external_id or conv.id}

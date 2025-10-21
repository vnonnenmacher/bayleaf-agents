# src/bayleaf_agents/agents/base_agent.py
import uuid, time, structlog, json
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from ..models import Conversation, Message, Role
from ..llm.base import LLMProvider
from ..tools.bayleaf import BayleafClient, tool_schemas
from ..auth.deps import Principal

log = structlog.get_logger("agent")

MAX_HISTORY_MSGS = 20


class BaseAgent:
    def __init__(
        self,
        name: str,
        objective: str | Dict[str, str],  # supports i18n dict or single string
        provider: LLMProvider,
        bayleaf: BayleafClient,
    ):
        self.name = name
        self.objective = objective
        self.provider = provider
        self.bayleaf = bayleaf

    def _get_or_create_conversation(
        self, db: Session, external_id: Optional[str], user_id: str, channel: str
    ) -> Conversation:
        conv = None
        if external_id:
            conv = (
                db.query(Conversation)
                .filter_by(external_id=external_id, user_id=user_id, channel=channel)
                .first()
            )
        if not conv:
            conv = Conversation(
                external_id=external_id, user_id=user_id, channel=channel
            )
            db.add(conv)
            db.commit()
            db.refresh(conv)
        return conv

    def _load_history(self, db: Session, conv_id: str) -> List[Dict[str, Any]]:
        q = (
            db.query(Message)
            .filter(Message.conversation_id == conv_id)
            .order_by(Message.created_at.asc())
            .limit(MAX_HISTORY_MSGS)
        )
        msgs: List[Dict[str, Any]] = []
        for m in q.all():
            if m.role == Role.tool:
                msgs.append(
                    {
                        "role": "tool",
                        "content": m.content,
                        "tool_call_id": m.tool_name or "tool",
                    }
                )
            else:
                msgs.append({"role": m.role.value, "content": m.content})
        return msgs

    # --- Tool execution (token-scoped; no IDs) ---
    def _execute_tool(
        self,
        name: str,
        *,
        principal: Optional[Principal] = None,
    ) -> Dict[str, Any] | List[Dict[str, Any]]:
        """
        Map tool name to Bayleaf client call.
        Tools must infer the patient from the bearer token; do not pass IDs.
        """
        if name in ("patient_summary", "current_patient_summary"):
            # Token-scoped: server infers the patient from the bearer token.
            return self.bayleaf.current_patient_summary(principal=principal)
        elif name in ("list_medications", "current_medications"):
            return self.bayleaf.current_medications(principal=principal)
        else:
            return {"error": f"unknown_tool:{name}"}

    def _get_objective(self, lang: str) -> str:
        """Pick objective text in the requested language, fallback to en-US."""
        if isinstance(self.objective, dict):
            return self.objective.get(lang, self.objective.get("en-US", ""))
        return str(self.objective)

    # --- Main chat loop (no IDs) ---
    def chat(
        self,
        db: Session,
        channel: str,
        user_message: str,
        external_conversation_id: Optional[str],
        *,
        principal: Optional[Principal] = None,
        lang: str = "pt-BR",
    ) -> Dict[str, Any]:
        trace = f"{self.name}_{uuid.uuid4().hex[:12]}"
        t0 = time.time()

        conv = self._get_or_create_conversation(
            db, external_conversation_id, principal.user_id, channel
        )

        system_prompt = (
            f"You are {self.name}. {self._get_objective(lang)}\n"
            f"Always respond ONLY in {lang}. If any tool data or user content is in another language, "
            f"translate it to {lang}.\n"
            "Format succinctly using short paragraphs and bullet lists when enumerating items. "
            "Avoid repeating raw JSON or units literally if they are confusing—explain them clearly."
        )
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self._load_history(db, conv.id))
        messages.append({"role": "user", "content": user_message})

        # persist user message
        db.add(Message(conversation_id=conv.id, role=Role.user, content=user_message))
        db.commit()

        tools = tool_schemas()
        out = self.provider.chat(messages, tools)

        used_tools: List[str] = []
        reply = out.get("reply", "Ok.")

        # handle tool calls
        if out.get("tool_calls"):
            # store simplified tool_calls for debugging
            db.add(
                Message(
                    conversation_id=conv.id,
                    role=Role.assistant,
                    content="",
                    tool_name="__tool_calls__",
                    tool_args={"calls": out["tool_calls"]},
                )
            )
            db.commit()

            # Convert simplified -> OpenAI wire shape
            oai_tool_calls = []
            for tc in out["tool_calls"]:
                oai_tool_calls.append({
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("args", {})),
                    },
                })

            messages.append(
                {"role": "assistant", "content": "", "tool_calls": oai_tool_calls}
            )

            # Execute each call and append tool results
            for tc in out["tool_calls"]:
                name = tc["name"]
                used_tools.append(name)

                result = self._execute_tool(name, principal=principal)

                # persist tool result
                db.add(
                    Message(
                        conversation_id=conv.id,
                        role=Role.tool,
                        content=json.dumps(result, ensure_ascii=False),
                        tool_name=name,
                        tool_result=result,
                    )
                )
                db.commit()

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", "tool"),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

            # get final answer after tool results
            final = self.provider.chat(messages, tools=[])
            reply = final.get("reply") or reply

        # persist assistant reply
        db.add(Message(conversation_id=conv.id, role=Role.assistant, content=reply))
        db.commit()

        log.info(
            "chat_done",
            agent=self.name,
            trace_id=trace,
            tools=used_tools,
            ms=int((time.time() - t0) * 1000),
        )
        return {
            "reply": reply,
            "used_tools": used_tools,
            "trace_id": trace,
            "conversation_id": conv.external_id or conv.id,
        }

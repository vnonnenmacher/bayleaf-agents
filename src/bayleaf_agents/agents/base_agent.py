# src/bayleaf_agents/agents/base_agent.py
import uuid, time, structlog, json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from ..models import Conversation, Message, Role, PHIEntity
from ..llm.base import LLMProvider
from ..tools.bayleaf import BayleafClient, tool_schemas
from ..auth.deps import Principal
from ..services.phi_filter import PHIFilterClient, PHIEntityResult
from .state_handlers import BaseStateHandler

log = structlog.get_logger("agent")

MAX_HISTORY_MSGS = 20
STATE_TOOL_NAME = "__state__"
PLACEHOLDER_GUIDANCE = (
    "PII placeholders may appear (e.g., <first_name>, <last_name>, <e_mail>, <phone_number>, <ssn>). "
    "These stand for valid user-provided values. Do NOT ask to re-enter them. "
    "Use placeholders directly in tool calls and responses, and keep them unchanged. "
    "Treat them as already validated."
)


class BaseAgent:
    def __init__(
        self,
        name: str,
        objective: str | Dict[str, str],  # supports i18n dict or single string
        provider: LLMProvider,
        bayleaf: BayleafClient,
        phi_filter: Optional[PHIFilterClient] = None,
        placeholder_instructions: Optional[str] = None,
        state_handler: Optional[BaseStateHandler] = None,
    ):
        self.log = structlog.get_logger("agent")
        self.name = name
        self.objective = objective
        self.provider = provider
        self.bayleaf = bayleaf
        self.phi_filter = phi_filter or PHIFilterClient()
        self.placeholder_instructions = placeholder_instructions or PLACEHOLDER_GUIDANCE
        self.state_handler = state_handler or BaseStateHandler(log=self.log)

    def _load_state(self, db: Session, conv_id: str) -> Dict[str, Any]:
        msg = (
            db.query(Message)
            .filter(Message.conversation_id == conv_id, Message.tool_name == STATE_TOOL_NAME)
            .order_by(Message.created_at.desc())
            .first()
        )
        if not msg:
            return {}
        if msg.tool_result:
            return dict(msg.tool_result)
        try:
            return json.loads(msg.content)
        except Exception:
            return {}

    def _save_state(self, db: Session, conv_id: str, state: Dict[str, Any]):
        db.add(
            Message(
                conversation_id=conv_id,
                role=Role.assistant,
                content=json.dumps(state, ensure_ascii=False),
                redacted_content=json.dumps(state, ensure_ascii=False),
                tool_name=STATE_TOOL_NAME,
                tool_result=state,
            )
        )
        db.commit()

    def _state_summary(self, state: Dict[str, Any]) -> str:
        summary = {
            "access_token": bool(state.get("access_token")),
            "selected_slot_id": state.get("selected_slot_id"),
            "last_slot_query": state.get("last_slot_query"),
            "last_slots_count": len(state.get("last_slots", [])) if isinstance(state.get("last_slots"), list) else 0,
            "last_professionals_count": len(state.get("last_professionals", [])) if isinstance(state.get("last_professionals"), list) else 0,
            "last_specializations_count": len(state.get("last_specializations", [])) if isinstance(state.get("last_specializations"), list) else 0,
            "last_booking": state.get("last_booking", {}).get("appointment_id") if isinstance(state.get("last_booking"), dict) else None,
        }
        return json.dumps(summary, ensure_ascii=False)

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

    def _load_history(self, db: Session, conv_id: str, *, include_tools: bool = False, lang: str = "en") -> List[Dict[str, Any]]:
        q = (
            db.query(Message)
            .filter(Message.conversation_id == conv_id)
            .order_by(Message.created_at.asc())
            .limit(MAX_HISTORY_MSGS)
        )
        msgs: List[Dict[str, Any]] = []
        for m in q.all():
            content = m.redacted_content or self._redact_and_store(db, m, lang=lang)
            if m.role == Role.tool:
                if include_tools:
                    msgs.append(
                        {
                            "role": "assistant",
                            "content": f"[tool:{m.tool_name or 'tool'}] {content}",
                        }
                    )
                # if include_tools is False, skip tool messages entirely
            else:
                msgs.append({"role": m.role.value, "content": content})
        return msgs

    def _redact_and_store(self, db: Session, message: Message, *, lang: str = "en") -> str:
        """
        Fetch (or compute) a redacted version of the message, persisting PHI hits.
        """
        if message.redacted_content:
            return message.redacted_content

        result = self.phi_filter.redact(message.content, language=lang) if self.phi_filter else {"redacted_text": message.content, "entities": []}
        redacted = result["redacted_text"]
        message.redacted_content = redacted
        db.add(message)
        db.commit()
        db.refresh(message)
        self._persist_phi_entities(db, message, result.get("entities", []))
        return redacted

    def _persist_phi_entities(self, db: Session, message: Message, entities: List[PHIEntityResult]):
        if not entities or not message.id:
            return
        existing = db.query(PHIEntity).filter(PHIEntity.message_id == message.id).first()
        if existing:
            return
        for ent in entities:
            db.add(
                PHIEntity(
                    conversation_id=message.conversation_id,
                    message_id=message.id,
                    entity_type=str(ent.get("entity_type") or "phi"),
                    placeholder=str(ent.get("placeholder") or "<phi>"),
                    original_text=str(ent.get("text") or ""),
                    start=ent.get("start"),
                    end=ent.get("end"),
                )
            )
        db.commit()

    def _placeholder_map(self, db: Session, conv_id: str) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        rows = (
            db.query(PHIEntity)
            .filter(PHIEntity.conversation_id == conv_id)
            .order_by(PHIEntity.created_at.asc())
            .all()
        )
        for ent in rows:
            if ent.placeholder:
                mapping[ent.placeholder] = ent.original_text
                # also allow lookups without angle brackets for leniency
                mapping[ent.placeholder.strip("<>")] = ent.original_text
        return mapping

    def _restore_placeholders(self, payload: Any, mapping: Dict[str, str]) -> Any:
        if isinstance(payload, str):
            restored = payload
            for placeholder, original in mapping.items():
                if placeholder in restored:
                    restored = restored.replace(placeholder, original)
            return restored
        if isinstance(payload, dict):
            return {k: self._restore_placeholders(v, mapping) for k, v in payload.items()}
        if isinstance(payload, list):
            return [self._restore_placeholders(item, mapping) for item in payload]
        return payload

    # --- Tool execution (token-scoped; no IDs) ---
    def _execute_tool(
        self,
        name: str,
        *,
        args: Optional[Dict[str, Any]] = None,
        principal: Optional[Principal] = None,
    ) -> Dict[str, Any] | List[Dict[str, Any]]:
        """
        Map tool name to Bayleaf client call.
        Some tools are token-scoped (no IDs); others expect explicit payload.
        """
        args = args or {}
        if name in ("patient_summary", "current_patient_summary"):
            # Token-scoped: server infers the patient from the bearer token.
            return self.bayleaf.current_patient_summary(principal=principal)
        elif name in ("list_medications", "current_medications"):
            return self.bayleaf.current_medications(principal=principal)
        elif name == "create_patient":
            try:
                log.info("tool_call", tool=name, args=args)
                return self.bayleaf.create_patient(
                    first_name=args["first_name"],
                    email=args["email"],
                    is_adult=bool(args.get("is_adult", True)),
                    principal=principal,
                )
            except KeyError as e:
                return {"error": f"missing_arg:{e.args[0]}"}
        elif name in ("list_available_slots", "available_slots"):
            log.info("tool_call", tool=name, args=args)
            return self.bayleaf.list_available_slots(
                principal=principal,
                start_date=args.get("start_date"),
                end_date=args.get("end_date"),
                service_id=args.get("service_id"),
            )
        elif name in ("list_available_professionals", "available_professionals"):
            log.info("tool_call", tool=name, args=args)
            return self.bayleaf.list_available_professionals(
                principal=principal,
                start_date=args.get("start_date"),
                end_date=args.get("end_date"),
                service_id=args.get("service_id"),
            )
        elif name in ("list_available_specializations", "available_specializations"):
            log.info("tool_call", tool=name, args=args)
            return self.bayleaf.list_available_specializations(
                principal=principal,
                start_date=args.get("start_date"),
                end_date=args.get("end_date"),
                service_id=args.get("service_id"),
            )
        elif name == "chat_token":
            try:
                log.info("tool_call", tool=name, args={k: v for k, v in args.items() if k != "password"})
                return self.bayleaf.chat_token(
                    email=args["email"],
                    password=args.get("password") or "password123",
                )
            except KeyError as e:
                return {"error": f"missing_arg:{e.args[0]}"}
        elif name == "book_appointment":
            try:
                log.info("tool_call", tool=name, args={k: v for k, v in args.items() if k != "access_token"})
                return self.bayleaf.book_appointment(
                    slot_id=args["slot_id"],
                    access_token=args.get("access_token"),
                    principal=principal,
                )
            except KeyError as e:
                return {"error": f"missing_arg:{e.args[0]}"}
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
        lang: str = "en-US",
    ) -> Dict[str, Any]:
        trace = f"{self.name}_{uuid.uuid4().hex[:12]}"
        t0 = time.time()
        lang_norm = (lang or "en").split("-")[0]

        conv = self._get_or_create_conversation(
            db, external_conversation_id, principal.user_id, channel
        )
        now_iso = datetime.now(timezone.utc).isoformat()

        system_prompt = (
            f"You are {self.name}. {self._get_objective(lang)}\n"
            f"Always respond ONLY in {lang}. If any tool data or user content is in another language, "
            f"translate it to {lang}.\n"
            "Format succinctly using short paragraphs and bullet lists when enumerating items. "
            "Avoid repeating raw JSON or units literally if they are confusing—explain them clearly.\n"
            f"Current datetime (UTC): {now_iso}\n"
            f"{self.placeholder_instructions}"
        )
        messages = [{"role": "system", "content": system_prompt}]
        state = self._load_state(db, conv.id)
        if state:
            messages.append({"role": "assistant", "content": f"[state] {self._state_summary(state)}"})
        messages.extend(self._load_history(db, conv.id, include_tools=True, lang=lang_norm))

        user_redaction = self.phi_filter.redact(user_message, language=lang_norm) if self.phi_filter else {"redacted_text": user_message, "entities": []}
        redacted_user_text = user_redaction["redacted_text"]
        try:
            self.log.info(
                "redaction_applied",
                role="user",
                changed=user_message != redacted_user_text,
                entities=len(user_redaction.get("entities", []) if isinstance(user_redaction.get("entities", []), list) else []),
                placeholders=[e.get("placeholder") for e in user_redaction.get("entities", []) if isinstance(e, dict)] if isinstance(user_redaction.get("entities", []), list) else [],
            )
        except Exception:
            pass
        if user_redaction.get("entities"):
            placeholders = [e.get("placeholder") for e in user_redaction.get("entities", []) if isinstance(e, dict)]
            self.log.info("phi_redaction_user", placeholders=placeholders, count=len(placeholders))
        messages.append({"role": "user", "content": redacted_user_text})
        # Hint to the model about what was provided without leaking raw PHI
        if user_redaction.get("entities"):
            provided = ", ".join(
                sorted({(e.get("entity_type") or e.get("placeholder") or "phi") for e in user_redaction.get("entities", []) if isinstance(e, dict)})
            )
            messages.append({"role": "assistant", "content": f"[redaction] user provided: {provided} (value hidden)"})

        # persist user message (raw + redacted + PHI entities)
        user_record = Message(conversation_id=conv.id, role=Role.user, content=user_message, redacted_content=redacted_user_text)
        db.add(user_record)
        db.commit()
        db.refresh(user_record)
        self._persist_phi_entities(db, user_record, user_redaction.get("entities", []))
        placeholder_mapping = self._placeholder_map(db, conv.id)

        tools = tool_schemas()
        out = self.provider.chat(messages, tools)

        used_tools: List[str] = []
        reply = out.get("reply", "Ok.")
        state_changed = False
        placeholder_mapping = self._placeholder_map(db, conv.id)

        # handle tool calls
        if out.get("tool_calls"):
            # store simplified tool_calls for debugging
            db.add(
                Message(
                    conversation_id=conv.id,
                    role=Role.assistant,
                    content="",
                    redacted_content="",
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
                prepared_args = self._restore_placeholders(tc.get("args", {}), placeholder_mapping)

                result = self._execute_tool(
                    name,
                    args=prepared_args,
                    principal=principal,
                )

                state_changed = self.state_handler.apply(
                    tool_name=name, args=prepared_args, result=result, state=state
                ) or state_changed

                # persist tool result
                tool_content = json.dumps(result, ensure_ascii=False)
                tool_redaction = self.phi_filter.redact(tool_content, language=lang_norm) if self.phi_filter else {"redacted_text": tool_content, "entities": []}
                try:
                    self.log.info(
                        "redaction_applied",
                        role="tool",
                        tool=name,
                        changed=tool_content != tool_redaction.get("redacted_text"),
                        entities=len(tool_redaction.get("entities", []) if isinstance(tool_redaction.get("entities", []), list) else []),
                        placeholders=[e.get("placeholder") for e in tool_redaction.get("entities", []) if isinstance(e, dict)] if isinstance(tool_redaction.get("entities", []), list) else [],
                    )
                except Exception:
                    pass
                tool_msg = Message(
                    conversation_id=conv.id,
                    role=Role.tool,
                    content=tool_content,
                    redacted_content=tool_redaction["redacted_text"],
                    tool_name=name,
                    tool_args=tc.get("args"),
                    tool_result=result,
                )
                db.add(tool_msg)
                db.commit()
                db.refresh(tool_msg)
                self._persist_phi_entities(db, tool_msg, tool_redaction.get("entities", []))
                placeholder_mapping = self._placeholder_map(db, conv.id)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", "tool"),
                        "content": tool_redaction["redacted_text"],
                    }
                )

            # get final answer after tool results
            final = self.provider.chat(messages, tools=[])
            reply = final.get("reply") or reply

        if state_changed:
            self._save_state(db, conv.id, state)

        # Restore placeholders for user-facing reply (keep redacted copy persisted)
        restored_reply = self._restore_placeholders(reply, placeholder_mapping)
        db.add(Message(conversation_id=conv.id, role=Role.assistant, content=restored_reply, redacted_content=reply))
        db.commit()

        log.info(
            "chat_done",
            agent=self.name,
            trace_id=trace,
            tools=used_tools,
            ms=int((time.time() - t0) * 1000),
        )
        return {
            "reply": restored_reply,
            "used_tools": used_tools,
            "trace_id": trace,
            "conversation_id": conv.external_id or conv.id,
        }

# src/bayleaf_agents/agents/base_agent.py
import uuid, time, structlog, json, re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from ..models import Conversation, Message, Role, PHIEntity
from ..llm.base import LLMProvider
from ..tools.bayleaf import BayleafClient, tool_schemas
from ..tools.documents import DocumentsToolset, query_tool_schemas
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
        documents_tools: Optional[DocumentsToolset] = None,
        phi_filter: Optional[PHIFilterClient] = None,
        use_phi_filter: bool = True,
        placeholder_instructions: Optional[str] = None,
        state_handler: Optional[BaseStateHandler] = None,
        enabled_tool_names: Optional[List[str]] = None,
        documents_doc_key: Optional[str] = None,
    ):
        self.log = structlog.get_logger("agent")
        self.name = name
        self.objective = objective
        self.provider = provider
        self.bayleaf = bayleaf
        self.documents_tools = documents_tools
        self.phi_filter = (phi_filter or PHIFilterClient()) if use_phi_filter else None
        self.placeholder_instructions = placeholder_instructions or PLACEHOLDER_GUIDANCE
        self.state_handler = state_handler or BaseStateHandler(log=self.log)
        self.enabled_tool_names = set(enabled_tool_names) if enabled_tool_names is not None else None
        self.documents_doc_key = documents_doc_key

    def _tool_enabled(self, name: str) -> bool:
        if self.enabled_tool_names is None:
            return True
        return name in self.enabled_tool_names

    def _available_tools(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        for schema in tool_schemas():
            tool_name = str(schema.get("name") or "")
            if tool_name and self._tool_enabled(tool_name):
                tools.append(schema)

        if self.documents_tools and self._tool_enabled("query_documents"):
            tools += query_tool_schemas()
        return tools

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
            "last_booking_error": state.get("last_booking_error", {}).get("status_code") if isinstance(state.get("last_booking_error"), dict) else None,  # noqa
        }
        return json.dumps(summary, ensure_ascii=False)

    def _get_or_create_conversation(
        self,
        db: Session,
        external_id: Optional[str],
        user_id: str,
        channel: str,
        agent_slug: Optional[str] = None,
        group_id: Optional[str] = None,
        initial_name: Optional[str] = None,
    ) -> Conversation:
        conv = None
        q = (
            db.query(Conversation)
            .filter_by(user_id=user_id, channel=channel)
        )
        if agent_slug is None:
            q = q.filter(Conversation.agent_slug.is_(None))
        else:
            q = q.filter_by(agent_slug=agent_slug)

        if external_id:
            # Prefer explicit external ids, then fall back to DB conversation id.
            conv = q.filter(Conversation.external_id == external_id).first()
            if not conv:
                conv = q.filter(Conversation.id == external_id).first()
        if conv and group_id is not None and conv.group_id != group_id:
            raise ValueError("conversation_group_mismatch")
        if not conv:
            conv = Conversation(
                external_id=external_id,
                user_id=user_id,
                channel=channel,
                agent_slug=agent_slug,
                group_id=group_id,
                name=initial_name or "New conversation",
            )
            db.add(conv)
            db.commit()
            db.refresh(conv)
        return conv

    def _conversation_title_from_first_message(self, user_message: str, *, max_words: int = 6) -> str:
        words = re.findall(r"[^\W_]+(?:['-][^\W_]+)*", user_message or "", flags=re.UNICODE)
        if not words:
            return "New conversation"
        return " ".join(words[:max_words])[:120]

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
        candidate_document_ids: Optional[List[str]] = None,
        forced_document_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any] | List[Dict[str, Any]]:
        """
        Map tool name to Bayleaf client call.
        Some tools are token-scoped (no IDs); others expect explicit payload.
        """
        args = args or {}
        if not self._tool_enabled(name):
            return {"error": f"tool_not_allowed:{name}"}
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
        elif name == "query_documents":
            if not self.documents_tools:
                return {"error": "documents_tool_unavailable"}
            try:
                query = str(args.get("query") or "")
                if not query:
                    return {"error": "missing_arg:query"}
                document_uuid = args.get("document_uuid")
                document_uuids = args.get("document_uuids")
                forced_ids = self._normalize_document_ids(forced_document_ids)
                if forced_ids:
                    if document_uuid and document_uuid in forced_ids:
                        document_uuids = [document_uuid]
                        document_uuid = None
                    elif document_uuid and document_uuid not in forced_ids:
                        document_uuids = forced_ids
                        document_uuid = None
                    elif document_uuids:
                        allowed = set(forced_ids)
                        document_uuids = [doc_id for doc_id in document_uuids if doc_id in allowed]
                        if not document_uuids:
                            document_uuids = forced_ids
                    else:
                        document_uuids = forced_ids
                    document_uuid = None
                elif not document_uuid and not document_uuids and candidate_document_ids:
                    document_uuids = candidate_document_ids
                return self.documents_tools.query_documents(
                    query=query,
                    top_k=int(args.get("top_k", 5)),
                    model_used=args.get("model_used"),
                    document_uuid=document_uuid,
                    document_uuids=document_uuids,
                    source_type=args.get("source_type"),
                    is_bayleaf=args.get("is_bayleaf"),
                    doc_key=self.documents_doc_key,
                    principal=principal,
                )
            except Exception as e:
                return {"error": "query_documents_failed", "details": str(e)}
        else:
            return {"error": f"unknown_tool:{name}"}

    def _normalize_document_ids(self, document_ids: Optional[List[str]]) -> List[str]:
        if not document_ids:
            return []
        seen: set[str] = set()
        normalized: List[str] = []
        for doc_id in document_ids:
            value = str(doc_id).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized

    def _safe_float(self, value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    def _chunk_ref(self, chunk: Dict[str, Any], fallback_index: int) -> str:
        doc_uuid = str(chunk.get("document_uuid") or "").strip() or "unknown-doc"
        chunk_index_raw = chunk.get("chunk_index")
        if isinstance(chunk_index_raw, int):
            chunk_index = chunk_index_raw
        else:
            try:
                chunk_index = int(str(chunk_index_raw))
            except Exception:
                chunk_index = fallback_index
        return f"{doc_uuid}#{chunk_index}"

    def _collect_retrieved_chunks(
        self,
        payload: Dict[str, Any],
        *,
        collected: List[Dict[str, Any]],
        seen_refs: set[str],
    ) -> None:
        chunks = payload.get("chunks")
        if not isinstance(chunks, list):
            return
        for idx, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                continue
            doc_uuid = str(chunk.get("document_uuid") or "").strip()
            doc_name = str(chunk.get("name") or "").strip()
            text_chunk = str(chunk.get("text_chunk") or "").strip()
            if not doc_uuid or not doc_name or not text_chunk:
                continue
            chunk_ref = self._chunk_ref(chunk, fallback_index=idx)
            if chunk_ref in seen_refs:
                continue
            seen_refs.add(chunk_ref)
            collected.append(
                {
                    "chunk_ref": chunk_ref,
                    "document_uuid": doc_uuid,
                    "document_name": doc_name,
                    "chunk_index": chunk.get("chunk_index"),
                    "text_chunk": text_chunk,
                    "score": self._safe_float(chunk.get("score")),
                }
            )

    def _collect_retrieved_chunks_from_group_context(
        self,
        group_context: Optional[Dict[str, Any]],
        *,
        collected: List[Dict[str, Any]],
        seen_refs: set[str],
    ) -> None:
        if not isinstance(group_context, dict):
            return
        retrieval_context = group_context.get("retrieval_context")
        if not isinstance(retrieval_context, dict):
            return
        self._collect_retrieved_chunks(retrieval_context, collected=collected, seen_refs=seen_refs)

    def _documents_from_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        seen: set[str] = set()
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            doc_uuid = str(chunk.get("document_uuid") or "").strip()
            doc_name = str(chunk.get("document_name") or "").strip()
            if not doc_uuid or not doc_name or doc_uuid in seen:
                continue
            seen.add(doc_uuid)
            out.append({"name": doc_name, "uuid": doc_uuid})
        return out

    def _documents_from_citations(self, citations: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        seen: set[str] = set()
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            doc_uuid = str(citation.get("document_uuid") or "").strip()
            doc_name = str(citation.get("document_name") or "").strip()
            if not doc_uuid or not doc_name or doc_uuid in seen:
                continue
            seen.add(doc_uuid)
            out.append({"name": doc_name, "uuid": doc_uuid})
        return out

    def _parse_json_object(self, raw: str) -> Optional[Dict[str, Any]]:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            return None
        except Exception:
            pass
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
            return None
        except Exception:
            return None

    def _extract_citations(
        self,
        *,
        answer: str,
        retrieved_chunks: List[Dict[str, Any]],
        lang: str,
    ) -> List[Dict[str, Any]]:
        if not answer.strip() or not retrieved_chunks:
            return []

        catalog: List[Dict[str, Any]] = []
        for chunk in retrieved_chunks:
            if not isinstance(chunk, dict):
                continue
            text_chunk = str(chunk.get("text_chunk") or "").strip()
            if not text_chunk:
                continue
            catalog.append(
                {
                    "chunk_ref": str(chunk.get("chunk_ref") or ""),
                    "document_uuid": str(chunk.get("document_uuid") or ""),
                    "document_name": str(chunk.get("document_name") or ""),
                    "score": chunk.get("score"),
                    "text_chunk": text_chunk[:1200],
                }
            )
        if not catalog:
            return []

        system_prompt = (
            "You are CitationExtractor.\n"
            "Given an answer and retrieved chunks, return ONLY strict JSON with this shape:\n"
            '{"citations":[{"id":"c1","document_uuid":"...","document_name":"...","chunk_ref":"doc#idx","evidence_text":"..."}]}\n'
            "Rules:\n"
            "- Only cite chunks that directly support concrete claims in the answer.\n"
            "- chunk_ref MUST be one of the provided chunk_ref values.\n"
            "- Keep evidence_text short (max 240 chars).\n"
            "- If no chunk directly supports the answer, return {\"citations\":[]}."
        )
        user_prompt = (
            f"Language: {lang}\n\n"
            f"Answer:\n{answer}\n\n"
            f"Retrieved chunks catalog:\n{json.dumps(catalog, ensure_ascii=False)}"
        )

        try:
            out = self.provider.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                tools=[],
            )
        except Exception:
            return []

        parsed = self._parse_json_object(str(out.get("reply") or ""))
        if not parsed:
            return []
        raw_citations = parsed.get("citations")
        if not isinstance(raw_citations, list):
            return []

        catalog_by_ref = {
            str(item.get("chunk_ref") or "").strip(): item
            for item in catalog
            if str(item.get("chunk_ref") or "").strip()
        }
        citations: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_refs: set[str] = set()
        next_idx = 1

        for item in raw_citations:
            if not isinstance(item, dict):
                continue
            chunk_ref = str(item.get("chunk_ref") or "").strip()
            source = catalog_by_ref.get(chunk_ref)
            if not source or chunk_ref in seen_refs:
                continue
            doc_uuid = str(item.get("document_uuid") or source.get("document_uuid") or "").strip()
            doc_name = str(item.get("document_name") or source.get("document_name") or "").strip()
            if not doc_uuid or not doc_name or doc_uuid != str(source.get("document_uuid") or "").strip():
                continue
            citation_id = str(item.get("id") or "").strip() or f"c{next_idx}"
            if citation_id in seen_ids:
                citation_id = f"c{next_idx}"
            evidence_text = str(item.get("evidence_text") or "").strip()
            if not evidence_text:
                evidence_text = str(source.get("text_chunk") or "")[:240]
            citations.append(
                {
                    "id": citation_id,
                    "document_uuid": doc_uuid,
                    "document_name": doc_name,
                    "chunk_ref": chunk_ref,
                    "evidence_text": evidence_text[:240],
                    "retrieval_score": self._safe_float(source.get("score")),
                }
            )
            seen_ids.add(citation_id)
            seen_refs.add(chunk_ref)
            next_idx += 1

        return citations

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
        candidate_document_ids: Optional[List[str]] = None,
        document_route_trace: Optional[Dict[str, Any]] = None,
        agent_slug: Optional[str] = None,
        group_id: Optional[str] = None,
        group_context: Optional[Dict[str, Any]] = None,
        forced_document_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        trace = f"{self.name}_{uuid.uuid4().hex[:12]}"
        t0 = time.time()
        lang_norm = (lang or "en").split("-")[0]

        conv = self._get_or_create_conversation(
            db,
            external_conversation_id,
            principal.user_id,
            channel,
            agent_slug=agent_slug,
            group_id=group_id,
            initial_name=self._conversation_title_from_first_message(user_message),
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        normalized_forced_ids = self._normalize_document_ids(forced_document_ids)

        system_prompt = (
            f"You are {self.name}. {self._get_objective(lang)}\n"
            f"Always respond ONLY in {lang}. If any tool data or user content is in another language, "
            f"translate it to {lang}.\n"
            "Format succinctly using short paragraphs and bullet lists when enumerating items. "
            "Avoid repeating raw JSON or units literally if they are confusing—explain them clearly.\n"
            f"Current datetime (UTC): {now_iso}\n"
            f"{self.placeholder_instructions}"
        )
        if candidate_document_ids:
            system_prompt += (
                "\nDocument retrieval context: if you decide to query documents, prioritize the candidate document ids "
                f"provided by orchestration: {candidate_document_ids}."
            )
        if group_context:
            system_prompt += (
                "\nConversation group context:\n"
                f"{json.dumps(group_context, ensure_ascii=False)}\n"
                "Treat this conversation as scoped to that project/event context."
            )
        if normalized_forced_ids:
            system_prompt += (
                "\nDocument retrieval requirement: when using query_documents, always use only these document_uuids: "
                f"{normalized_forced_ids}."
            )
        messages = [{"role": "system", "content": system_prompt}]
        state = self._load_state(db, conv.id)
        if state:
            messages.append({"role": "assistant", "content": f"[state] {self._state_summary(state)}"})
        messages.extend(self._load_history(db, conv.id, include_tools=True, lang=lang_norm))

        user_redaction = self.phi_filter.redact(user_message, language=lang_norm) if self.phi_filter else {"redacted_text": user_message, "entities": []}  # noqa
        redacted_user_text = user_redaction["redacted_text"]
        try:
            self.log.info(
                "redaction_applied",
                role="user",
                changed=user_message != redacted_user_text,
                entities=len(user_redaction.get("entities", []) if isinstance(user_redaction.get("entities", []), list) else []),
                placeholders=[e.get("placeholder") for e in user_redaction.get("entities", []) if isinstance(e, dict)] if isinstance(
                    user_redaction.get("entities", []), list) else [],
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
        user_record = Message(
            conversation_id=conv.id,
            role=Role.user,
            content=user_message,
            redacted_content=redacted_user_text,
            retrieval_trace=document_route_trace,
        )
        db.add(user_record)
        db.commit()
        db.refresh(user_record)
        self._persist_phi_entities(db, user_record, user_redaction.get("entities", []))
        placeholder_mapping = self._placeholder_map(db, conv.id)

        tools = self._available_tools()
        out = self.provider.chat(messages, tools)

        used_tools: List[str] = []
        retrieved_chunks: List[Dict[str, Any]] = []
        retrieved_chunk_refs: set[str] = set()
        self._collect_retrieved_chunks_from_group_context(
            group_context,
            collected=retrieved_chunks,
            seen_refs=retrieved_chunk_refs,
        )
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
                    candidate_document_ids=candidate_document_ids,
                    forced_document_ids=normalized_forced_ids,
                )
                if name == "query_documents" and isinstance(result, dict):
                    self._collect_retrieved_chunks(
                        result,
                        collected=retrieved_chunks,
                        seen_refs=retrieved_chunk_refs,
                    )

                state_changed = self.state_handler.apply(
                    tool_name=name, args=prepared_args, result=result, state=state
                ) or state_changed

                # persist tool result
                tool_content = json.dumps(result, ensure_ascii=False)
                tool_redaction = self.phi_filter.redact(tool_content, language=lang_norm) if self.phi_filter else {"redacted_text": tool_content, "entities": []}  # noqa
                try:
                    self.log.info(
                        "redaction_applied",
                        role="tool",
                        tool=name,
                        changed=tool_content != tool_redaction.get("redacted_text"),
                        entities=len(tool_redaction.get("entities", []) if isinstance(tool_redaction.get("entities", []), list) else []),
                        placeholders=[e.get("placeholder") for e in tool_redaction.get("entities", []) if isinstance(e, dict)] if isinstance(tool_redaction.get("entities", []), list) else [],  # noqa
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
                    retrieval_trace=result.get("trace") if isinstance(result, dict) else None,
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
        citations = self._extract_citations(
            answer=reply,
            retrieved_chunks=retrieved_chunks,
            lang=lang,
        )
        cited_documents = self._documents_from_citations(citations)
        retrieved_documents = self._documents_from_chunks(retrieved_chunks)
        db.add(
            Message(
                conversation_id=conv.id,
                role=Role.assistant,
                content=restored_reply,
                redacted_content=reply,
                cited_documents=cited_documents,
                citations=citations,
            )
        )
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
            "cited_documents": cited_documents,
            "retrieved_documents": retrieved_documents,
            "citations": citations,
            "trace_id": trace,
            "conversation_id": conv.external_id or conv.id,
            "conversation_name": conv.name,
        }

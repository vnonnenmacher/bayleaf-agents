# src/bayleaf_agents/agents/base_agent.py
import uuid, time, structlog, json, re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from ..models import Conversation, Message, Role, PHIEntity, AgentRequest, AgentRequestState
from ..llm.base import LLMProvider
from ..tools.bayleaf import BayleafClient, tool_schemas
from ..tools.documents import DocumentsToolset, query_tool_schemas
from ..auth.deps import Principal
from ..services.phi_filter import PHIFilterClient, PHIEntityResult
from ..skills.document_decider import DocumentDeciderAgent
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
        decider_provider: Optional[LLMProvider] = None,
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
        self.decider_provider = decider_provider or provider

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

    def _save_state(self, db: Session, conv_id: str, state: Dict[str, Any], *, agent_request_id: Optional[str] = None):
        db.add(
            Message(
                conversation_id=conv_id,
                agent_request_id=agent_request_id,
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

    def _build_decider(self) -> Optional[DocumentDeciderAgent]:
        if not self.documents_tools:
            return None
        if not callable(getattr(self.documents_tools, "documents_available", None)):
            return None
        provider = getattr(self, "decider_provider", None) or self.provider
        return DocumentDeciderAgent(provider=provider, documents_tools=self.documents_tools)

    def _tokenize(self, text: str) -> List[str]:
        return [t for t in re.findall(r"[a-zA-ZÀ-ÿ0-9]+", (text or "").lower()) if len(t) >= 4]

    def _latest_query_documents_message(self, db: Session, conversation_id: Optional[str]) -> Optional[Message]:
        if not conversation_id:
            return None
        return (
            db.query(Message)
            .filter(
                Message.conversation_id == conversation_id,
                Message.role == Role.tool,
                Message.tool_name == "query_documents",
            )
            .order_by(Message.created_at.desc())
            .first()
        )

    def _latest_query_documents_chunks(self, db: Session, conversation_id: Optional[str]) -> List[Dict[str, Any]]:
        msg = self._latest_query_documents_message(db, conversation_id)
        if not msg or not isinstance(msg.tool_result, dict):
            return []
        chunks = msg.tool_result.get("chunks")
        if not isinstance(chunks, list):
            return []
        return [c for c in chunks if isinstance(c, dict)]

    def _has_explicit_recent_evidence(
        self,
        *,
        user_message: str,
        chunks: List[Dict[str, Any]],
    ) -> Tuple[bool, int]:
        if not chunks:
            return False, 0
        user_terms = set(self._tokenize(user_message))
        if not user_terms:
            return False, 0
        matched_chunks = 0
        for chunk in chunks:
            text = str(chunk.get("text_chunk") or "")
            chunk_terms = set(self._tokenize(text))
            overlap = user_terms.intersection(chunk_terms)
            if len(overlap) >= 3:
                matched_chunks += 1
        return matched_chunks > 0, matched_chunks

    def _is_high_risk_question(self, user_message: str) -> bool:
        return False

    def _user_turns_since_message(self, db: Session, conversation_id: Optional[str], message_ts: Optional[datetime]) -> int:
        if not conversation_id or not message_ts:
            return 999
        return int(
            db.query(Message)
            .filter(
                Message.conversation_id == conversation_id,
                Message.role == Role.user,
                Message.created_at > message_ts,
            )
            .count()
        )

    def _has_query_shift(self, *, user_message: str, chunks: List[Dict[str, Any]]) -> bool:
        return False

    def _prefetch_chunk_key(self, chunk: Dict[str, Any], fallback_index: int) -> str:
        doc_uuid = str(chunk.get("document_uuid") or "").strip() or "unknown-doc"
        raw_index = chunk.get("chunk_index")
        if isinstance(raw_index, int):
            chunk_index = raw_index
        else:
            try:
                chunk_index = int(str(raw_index))
            except Exception:
                chunk_index = fallback_index
        return f"{doc_uuid}#{chunk_index}"

    def _merge_prefetch_results(self, *results: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        merged_chunks: List[Dict[str, Any]] = []
        seen_keys: set[str] = set()
        query = ""
        model_used = ""
        top_k = 0
        traces: List[Dict[str, Any]] = []

        for result in results:
            if not isinstance(result, dict):
                continue
            if not query:
                query = str(result.get("query") or "")
            if not model_used:
                model_used = str(result.get("model_used") or "")
            try:
                top_k += int(result.get("top_k") or 0)
            except Exception:
                pass
            trace = result.get("trace")
            if isinstance(trace, dict):
                traces.append(trace)
            chunks = result.get("chunks")
            if not isinstance(chunks, list):
                continue
            for idx, chunk in enumerate(chunks):
                if not isinstance(chunk, dict):
                    continue
                key = self._prefetch_chunk_key(chunk, fallback_index=idx)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                merged_chunks.append(chunk)

        return {
            "query": query,
            "top_k": top_k,
            "model_used": model_used,
            "chunks": merged_chunks,
            "trace": {
                "strategy": "dual_prefetch" if len(traces) > 1 else "single_prefetch",
                "source_traces": traces,
                "returned_chunks": len(merged_chunks),
            },
        }

    def _lab_retrieval_evidence_policy(self, group_context: Optional[Dict[str, Any]]) -> str:
        if not isinstance(group_context, dict):
            return ""
        retrieval_context = group_context.get("retrieval_context")
        if not isinstance(retrieval_context, dict):
            return ""
        chunks = retrieval_context.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            return ""
        doc_key = str(self.documents_doc_key or "").strip().lower()
        if not doc_key.startswith("lab"):
            return ""
        return (
            "\nEvidence policy:\n"
            "- Retrieved chunks in Conversation group context are internal laboratory SOP/compliance evidence.\n"
            "- For procedure/compliance questions, prioritize these chunks over general knowledge.\n"
            "- If a chunk directly answers the question, respond directly from that evidence and keep it concise.\n"
            "- Do not invent external tube orders or procedural steps not present in retrieved evidence.\n"
            "- If evidence is insufficient, state what is missing before giving a generic answer."
        )

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
    ) -> AgentRequest:
        """
        Submit-only entrypoint: resolves/creates the conversation, persists an AgentRequest
        (waiting) plus the initial user message (already linked to both), schedules
        _process_chat asynchronously, and returns immediately.
        """
        from ..services.agent_requests import find_active_agent_request_for_conversation, schedule_process_chat

        user_id = principal.user_id if principal else None
        effective_agent_slug = agent_slug or self.name

        conv = self._get_or_create_conversation(
            db,
            external_conversation_id,
            principal.user_id,
            channel,
            agent_slug=effective_agent_slug,
            group_id=group_id,
            initial_name=self._conversation_title_from_first_message(user_message),
        )

        if user_id and find_active_agent_request_for_conversation(db, conv.id, user_id):
            raise ValueError("active_request_conflict")

        payload: Dict[str, Any] = {
            "channel": channel,
            "user_message": user_message,
            "conversation_id": conv.id,
            "lang": lang,
            "candidate_document_ids": candidate_document_ids,
            "document_route_trace": document_route_trace,
            "agent_slug": effective_agent_slug,
            "group_id": group_id,
            "group_context": group_context,
            "forced_document_ids": forced_document_ids,
            "principal": (
                {
                    "user_id": principal.user_id,
                    "sub": principal.sub,
                    "scopes": principal.scopes,
                    "patient_id": principal.patient_id,
                    "raw": principal.raw,
                    "raw_token": principal.raw_token,
                }
                if principal
                else None
            ),
        }

        agent_request = AgentRequest(
            user_id=user_id or "",
            agent_slug=effective_agent_slug,
            channel=channel,
            state=AgentRequestState.waiting,
            payload=payload,
        )
        db.add(agent_request)
        db.commit()
        db.refresh(agent_request)

        db.add(
            Message(
                agent_request_id=agent_request.id,
                conversation_id=conv.id,
                role=Role.user,
                content=user_message,
            )
        )
        db.commit()

        log.info(
            "agent_request_created",
            agent_request_id=agent_request.id,
            user_id=user_id,
            agent_slug=effective_agent_slug,
            channel=channel,
            conversation_id=conv.id,
        )

        schedule_process_chat(agent_request.id, payload)
        return agent_request

    def _process_chat(
        self,
        db: Session,
        channel: str,
        user_message: str,
        conversation_id: str,
        *,
        principal: Optional[Principal] = None,
        lang: str = "en-US",
        candidate_document_ids: Optional[List[str]] = None,
        document_route_trace: Optional[Dict[str, Any]] = None,
        agent_slug: Optional[str] = None,
        group_id: Optional[str] = None,
        group_context: Optional[Dict[str, Any]] = None,
        forced_document_ids: Optional[List[str]] = None,
        agent_request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        trace = f"{self.name}_{uuid.uuid4().hex[:12]}"
        t0 = time.time()
        lang_norm = (lang or "en").split("-")[0]

        agent_request: Optional[AgentRequest] = None
        if agent_request_id:
            agent_request = db.query(AgentRequest).filter(AgentRequest.id == agent_request_id).first()
            if agent_request and agent_request.state == AgentRequestState.cancelled:
                log.info("agent_request_cancelled", agent_request_id=agent_request_id)
                return {}
            if agent_request:
                agent_request.state = AgentRequestState.processing
                agent_request.started_at = datetime.now(timezone.utc)
                db.add(agent_request)
                db.commit()
                log.info("agent_request_state_changed", agent_request_id=agent_request_id, state="processing")

        try:
            result = self._run_chat(
                db,
                channel,
                user_message,
                conversation_id,
                principal=principal,
                lang=lang,
                lang_norm=lang_norm,
                candidate_document_ids=candidate_document_ids,
                document_route_trace=document_route_trace,
                agent_slug=agent_slug,
                group_id=group_id,
                group_context=group_context,
                forced_document_ids=forced_document_ids,
                agent_request_id=agent_request_id,
                trace=trace,
                t0=t0,
            )
        except Exception as exc:
            if agent_request:
                db.rollback()
                agent_request = db.query(AgentRequest).filter(AgentRequest.id == agent_request_id).first()
                if agent_request:
                    agent_request.state = AgentRequestState.failed
                    agent_request.error_message = str(exc)
                    agent_request.finished_at = datetime.now(timezone.utc)
                    db.add(agent_request)
                    db.commit()
                log.error("agent_request_failed", agent_request_id=agent_request_id, error=str(exc))
            raise

        if agent_request:
            agent_request.state = AgentRequestState.succeeded
            agent_request.finished_at = datetime.now(timezone.utc)
            db.add(agent_request)
            db.commit()
            log.info("agent_request_state_changed", agent_request_id=agent_request_id, state="succeeded")

        return result

    def _run_chat(
        self,
        db: Session,
        channel: str,
        user_message: str,
        conversation_id: str,
        *,
        principal: Optional[Principal] = None,
        lang: str = "en-US",
        lang_norm: str = "en",
        candidate_document_ids: Optional[List[str]] = None,
        document_route_trace: Optional[Dict[str, Any]] = None,
        agent_slug: Optional[str] = None,
        group_id: Optional[str] = None,
        group_context: Optional[Dict[str, Any]] = None,
        forced_document_ids: Optional[List[str]] = None,
        agent_request_id: Optional[str] = None,
        trace: str = "",
        t0: float = 0.0,
    ) -> Dict[str, Any]:
        # chat() always resolves/creates the conversation synchronously; this path never creates one.
        conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not conv:
            raise ValueError(f"conversation_not_found:{conversation_id}")

        effective_candidate_ids = list(candidate_document_ids or [])
        effective_document_route_trace: Dict[str, Any] = dict(document_route_trace or {})
        effective_group_context = dict(group_context or {})
        prefetch_result: Optional[Dict[str, Any]] = None
        prefetch_top_k = 5

        decider = None
        if self.documents_tools and self._tool_enabled("query_documents"):
            decider = self._build_decider()
        if decider:
            route_trace: Dict[str, Any] = {"decider": None}
            forced_retrieval_reason: Optional[str] = None
            latest_chunks = self._latest_query_documents_chunks(db, conv.id)
            latest_tool_msg = self._latest_query_documents_message(db, conv.id)
            has_recent_evidence, evidence_hits = self._has_explicit_recent_evidence(
                user_message=user_message,
                chunks=latest_chunks,
            )
            high_risk_question = self._is_high_risk_question(user_message)
            query_shift = self._has_query_shift(user_message=user_message, chunks=latest_chunks)
            turns_since_last_retrieval = self._user_turns_since_message(
                db,
                conv.id,
                (latest_tool_msg.created_at if latest_tool_msg else None),
            )
            should_retrieve = False
            routing_mode = "no_decider"

            decision = decider.decide_documents(
                db=db,
                conversation_id=conv.id,
                user_message=user_message,
                lang=lang,
                principal=principal,
                doc_key=self.documents_doc_key,
            )
            route_trace["decider"] = decision
            available_count = int(decision.get("available_documents_count") or 0)
            decision_candidate_ids = list(decision.get("candidate_document_ids") or [])
            if decision.get("needs_retrieval"):
                effective_candidate_ids = decision_candidate_ids or effective_candidate_ids
                should_retrieve = True
                routing_mode = "decider_retrieval"
            elif high_risk_question and available_count > 0:
                should_retrieve = True
                routing_mode = "forced_high_risk"
                forced_retrieval_reason = "high_risk_question"
            elif query_shift and available_count > 0:
                should_retrieve = True
                routing_mode = "forced_query_shift"
                forced_retrieval_reason = "query_shift_detected"
            elif turns_since_last_retrieval > 1 and available_count > 0:
                should_retrieve = True
                routing_mode = "forced_stale_reuse_window"
                forced_retrieval_reason = "reuse_window_exceeded"
            elif available_count > 0 and not has_recent_evidence:
                should_retrieve = True
                routing_mode = "forced_fallback_no_evidence"
                forced_retrieval_reason = "no_explicit_recent_evidence"
            elif has_recent_evidence:
                routing_mode = "reuse_recent_evidence"
            else:
                routing_mode = "skip_no_catalog"

            route_trace["policy"] = {
                "has_recent_evidence": has_recent_evidence,
                "evidence_hits": evidence_hits,
                "high_risk_question": high_risk_question,
                "query_shift": query_shift,
                "turns_since_last_retrieval": turns_since_last_retrieval,
                "should_retrieve": should_retrieve,
                "fallback_forced": bool(not decision.get("needs_retrieval") and should_retrieve),
                "routing_mode": routing_mode,
                "forced_retrieval_reason": forced_retrieval_reason,
            }
            try:
                self.log.info(
                    "retrieval_routing_decision",
                    decider=decision,
                    has_recent_evidence=has_recent_evidence,
                    evidence_hits=evidence_hits,
                    high_risk_question=high_risk_question,
                    query_shift=query_shift,
                    turns_since_last_retrieval=turns_since_last_retrieval,
                    should_retrieve=should_retrieve,
                    fallback_forced=bool(not decision.get("needs_retrieval") and should_retrieve),
                    routing_mode=routing_mode,
                    forced_retrieval_reason=forced_retrieval_reason,
                )
            except Exception:
                pass

            if forced_retrieval_reason:
                try:
                    self.log.info(
                        "reuse_guard_trigger",
                        reason=forced_retrieval_reason,
                        routing_mode=routing_mode,
                        query=user_message,
                    )
                except Exception:
                    pass

            if should_retrieve and self.documents_tools:
                general_top_k = 10 if effective_candidate_ids else 5
                prefetch_top_k = general_top_k
                general_result = self.documents_tools.query_documents(
                    query=user_message,
                    top_k=general_top_k,
                    document_uuids=None,
                    doc_key=self.documents_doc_key,
                    principal=principal,
                )
                focused_result: Optional[Dict[str, Any]] = None
                if effective_candidate_ids:
                    focused_top_k = 10
                    prefetch_top_k = general_top_k + focused_top_k
                    focused_result = self.documents_tools.query_documents(
                        query=user_message,
                        top_k=focused_top_k,
                        document_uuids=effective_candidate_ids,
                        doc_key=None,
                        principal=principal,
                    )
                prefetch_result = self._merge_prefetch_results(focused_result, general_result)
                route_trace["prefetch"] = {
                    "requested_query": user_message,
                    "prefetch_strategy": ("dual_general_plus_candidates" if effective_candidate_ids else "single_general"),
                    "general_requested_top_k": general_top_k,
                    "focused_requested_top_k": (10 if effective_candidate_ids else None),
                    "candidate_document_ids": effective_candidate_ids,
                    "returned_chunks": len((prefetch_result or {}).get("chunks") or []),
                    "trace": (prefetch_result or {}).get("trace"),
                    "general_trace": (general_result or {}).get("trace"),
                    "focused_trace": (focused_result or {}).get("trace") if isinstance(focused_result, dict) else None,
                }
                try:
                    self.log.info(
                        "retrieval_prefetch_done",
                        requested_query=user_message,
                        prefetch_strategy=("dual_general_plus_candidates" if effective_candidate_ids else "single_general"),
                        general_requested_top_k=general_top_k,
                        focused_requested_top_k=(10 if effective_candidate_ids else None),
                        candidate_document_ids=effective_candidate_ids,
                        returned_chunks=len((prefetch_result or {}).get("chunks") or []),
                        trace=(prefetch_result or {}).get("trace"),
                        general_trace=(general_result or {}).get("trace"),
                        focused_trace=(focused_result or {}).get("trace") if isinstance(focused_result, dict) else None,
                        routing_mode=routing_mode,
                    )
                except Exception:
                    pass

            if prefetch_result:
                chunks = (prefetch_result.get("chunks") or []) if isinstance(prefetch_result, dict) else []
                effective_group_context["retrieval_context"] = {
                    "query": user_message,
                    "chunks": [
                        {
                            "document_uuid": c.get("document_uuid"),
                            "name": c.get("name"),
                            "chunk_index": c.get("chunk_index"),
                            "score": c.get("score"),
                            "text_chunk": str(c.get("text_chunk") or "")[:1400],
                        }
                        for c in chunks[:prefetch_top_k]
                        if isinstance(c, dict)
                    ],
                    "trace": (prefetch_result.get("trace") if isinstance(prefetch_result, dict) else None),
                }

            effective_document_route_trace.update(route_trace)

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
        if effective_candidate_ids:
            system_prompt += (
                "\nDocument retrieval context: if you decide to query documents, prioritize the candidate document ids "
                f"provided by orchestration: {effective_candidate_ids}."
            )
        if effective_group_context:
            system_prompt += (
                "\nConversation group context:\n"
                f"{json.dumps(effective_group_context, ensure_ascii=False)}\n"
                "Treat this conversation as scoped to that project/event context."
            )
            system_prompt += self._lab_retrieval_evidence_policy(effective_group_context)
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

        # persist user message (raw + redacted + PHI entities).
        # When submitted via chat(), the row already exists (created with conversation_id set)
        # and just needs redacted_content/retrieval_trace filled in here.
        user_record = None
        if agent_request_id:
            user_record = (
                db.query(Message)
                .filter(Message.agent_request_id == agent_request_id, Message.role == Role.user)
                .order_by(Message.created_at.asc())
                .first()
            )
        if user_record:
            user_record.conversation_id = conv.id
            user_record.redacted_content = redacted_user_text
            user_record.retrieval_trace = effective_document_route_trace
        else:
            user_record = Message(
                conversation_id=conv.id,
                agent_request_id=agent_request_id,
                role=Role.user,
                content=user_message,
                redacted_content=redacted_user_text,
                retrieval_trace=effective_document_route_trace,
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
            effective_group_context,
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
                    agent_request_id=agent_request_id,
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
                    candidate_document_ids=effective_candidate_ids,
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
                    agent_request_id=agent_request_id,
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
            self._save_state(db, conv.id, state, agent_request_id=agent_request_id)

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
                agent_request_id=agent_request_id,
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

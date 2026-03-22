import inspect
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth.deps import Principal, require_auth
from ..db import get_db
from ..models import Conversation, ConversationGroup, ConversationGroupType, Message, Role, UserMetadata
from ..schemas.chat import (
    ChatRequest,
    ChatResponse,
    ConversationMessage,
    ConversationMessagesResponse,
    ConversationGroupCreateRequest,
    ConversationGroupSummary,
    ConversationGroupsResponse,
    ConversationGroupPutRequest,
    ConversationGroupUpdateRequest,
    ConversationsResponse,
    ConversationSummary,
    PaginationInfo,
    SafetyInfo,
    UserMetadataResponse,
    UserMetadataUpsertRequest,
)
from ..services.agent_registry import discover_agents
from ..services.factories import (
    get_bayleaf,
    get_decider_provider,
    get_documents_tools,
    get_phi_filter,
    get_provider,
)
from ..tools.bayleaf import BayleafAuthError

router = APIRouter(prefix="/agents", tags=["agents"])
_AGENT_CLASSES = discover_agents()


def _require_user_id(principal: Principal) -> str:
    if not principal.user_id:
        raise HTTPException(status_code=401, detail="missing_user_id_claim")
    return principal.user_id


def _resolve_user_conversation(
    db: Session,
    *,
    user_id: str,
    conversation_identifier: str,
    channel: str | None = None,
    agent_slug: str | None = None,
) -> Conversation | None:
    q = db.query(Conversation).filter(Conversation.user_id == user_id)
    if channel:
        q = q.filter(Conversation.channel == channel)
    if agent_slug:
        q = q.filter(Conversation.agent_slug == agent_slug)
    conv = q.filter(Conversation.id == conversation_identifier).first()
    if conv:
        return conv
    return q.filter(Conversation.external_id == conversation_identifier).first()


def _normalize_document_uuids(document_uuids: list[str] | None) -> list[str]:
    if not document_uuids:
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in document_uuids:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _resolve_owned_group(db: Session, *, owner_id: str, group_id: str) -> ConversationGroup:
    group = (
        db.query(ConversationGroup)
        .filter(ConversationGroup.id == group_id, ConversationGroup.owner_id == owner_id)
        .first()
    )
    if not group:
        raise HTTPException(status_code=404, detail="group_not_found")
    return group


def _group_summary(group: ConversationGroup) -> ConversationGroupSummary:
    return ConversationGroupSummary(
        id=group.id,
        owner_id=group.owner_id,
        type=group.type.value,
        is_active=group.is_active,
        metadata=group.metadata_json or {},
        document_uuids=_normalize_document_uuids(group.document_uuids),
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


def _user_metadata_summary(item: UserMetadata) -> UserMetadataResponse:
    return UserMetadataResponse(
        owner_id=item.owner_id,
        metadata=item.metadata_json or {},
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


for slug, AgentCls in _AGENT_CLASSES.items():

    async def chat_endpoint(
        req: ChatRequest,
        db: Session = Depends(get_db),
        principal: Principal = Depends(require_auth()),
        _AgentCls=AgentCls,
        _slug=slug,
    ):
        user_id = _require_user_id(principal)
        group = None
        if req.group_id:
            group = _resolve_owned_group(db, owner_id=user_id, group_id=req.group_id)
            if not group.is_active:
                raise HTTPException(status_code=422, detail="group_inactive")

        forced_doc_ids = _normalize_document_uuids(req.document_uuids)
        if group:
            forced_doc_ids = _normalize_document_uuids(
                forced_doc_ids + _normalize_document_uuids(group.document_uuids)
            )

        group_context: dict[str, Any] | None = None
        if group:
            group_context = {
                "group_id": group.id,
                "type": group.type.value,
                "metadata": group.metadata_json or {},
                "document_uuids": _normalize_document_uuids(group.document_uuids),
            }

        common_kwargs = {
            "provider": get_provider(),
            "bayleaf": get_bayleaf(),
            "phi_filter": get_phi_filter(),
            "documents_tools": get_documents_tools(),
            "decider_provider": get_decider_provider(),
        }
        init_params = inspect.signature(_AgentCls.__init__).parameters
        accepted = {k: v for k, v in common_kwargs.items() if k in init_params}
        agent = _AgentCls(**accepted)
        try:
            result = agent.chat(
                db=db,
                channel=req.channel,
                user_message=req.message,
                external_conversation_id=req.conversation_id,
                principal=principal,  # token goes through; server infers patient
                lang=req.lang or "pt-BR",
                agent_slug=_slug,
                group_id=group.id if group else None,
                group_context=group_context,
                forced_document_ids=forced_doc_ids,
            )
        except ValueError as exc:
            if str(exc) == "conversation_group_mismatch":
                raise HTTPException(status_code=409, detail="conversation_group_mismatch") from exc
            raise
        except BayleafAuthError as exc:
            if exc.status_code == 401 and exc.error == "token_expired":
                raise HTTPException(
                    status_code=401,
                    detail={"error": "token_expired", "details": exc.details},
                ) from exc
            raise
        safety = SafetyInfo(triage="non-urgent")
        return ChatResponse(
            reply=result["reply"],
            used_tools=result["used_tools"],
            research_documents=result.get("research_documents", []),
            safety=safety,
            trace_id=result["trace_id"],
            conversation_id=result["conversation_id"],
            conversation_name=result["conversation_name"],
        )

    router.add_api_route(
        f"/{slug}/chat",
        chat_endpoint,
        methods=["POST"],
        response_model=ChatResponse,
        name=f"{slug}-chat",
    )


@router.post("/conversation-groups", response_model=ConversationGroupSummary)
async def create_conversation_group(
    req: ConversationGroupCreateRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_auth()),
):
    owner_id = _require_user_id(principal)
    group = ConversationGroup(
        owner_id=owner_id,
        type=ConversationGroupType(req.type),
        is_active=bool(req.is_active),
        metadata_json=req.metadata or {},
        document_uuids=_normalize_document_uuids(req.document_uuids),
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return _group_summary(group)


@router.patch("/conversation-groups/{group_id}", response_model=ConversationGroupSummary)
async def update_conversation_group(
    group_id: str,
    req: ConversationGroupUpdateRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_auth()),
):
    owner_id = _require_user_id(principal)
    group = _resolve_owned_group(db, owner_id=owner_id, group_id=group_id)
    if req.is_active is not None:
        group.is_active = req.is_active
    if req.metadata is not None:
        group.metadata_json = req.metadata
    if req.document_uuids is not None:
        group.document_uuids = _normalize_document_uuids(req.document_uuids)
    db.add(group)
    db.commit()
    db.refresh(group)
    return _group_summary(group)


@router.put("/conversation-groups/{group_id}", response_model=ConversationGroupSummary)
async def put_conversation_group(
    group_id: str,
    req: ConversationGroupPutRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_auth()),
):
    owner_id = _require_user_id(principal)
    group = _resolve_owned_group(db, owner_id=owner_id, group_id=group_id)
    group.metadata_json = req.metadata or {}
    group.document_uuids = _normalize_document_uuids(req.document_uuids)
    db.add(group)
    db.commit()
    db.refresh(group)
    return _group_summary(group)


@router.get("/conversation-groups", response_model=ConversationGroupsResponse)
async def list_conversation_groups(
    type: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_auth()),
):
    owner_id = _require_user_id(principal)
    query = db.query(ConversationGroup).filter(ConversationGroup.owner_id == owner_id)
    if type:
        try:
            group_type = ConversationGroupType(type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid_group_type_filter") from exc
        query = query.filter(ConversationGroup.type == group_type)
    if is_active is not None:
        query = query.filter(ConversationGroup.is_active == is_active)

    total = query.count()
    rows = (
        query.order_by(ConversationGroup.updated_at.desc(), ConversationGroup.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = [_group_summary(group) for group in rows]
    return ConversationGroupsResponse(
        items=items,
        pagination=PaginationInfo(
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + len(items)) < total,
        ),
    )


@router.get("/conversations", response_model=ConversationsResponse)
async def list_conversations(
    agent_slug: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    group_id: str | None = Query(default=None),
    without_group: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_auth()),
):
    user_id = _require_user_id(principal)

    activity_subq = (
        db.query(
            Message.conversation_id.label("conversation_id"),
            func.max(Message.created_at).label("last_message_at"),
            func.count(Message.id).label("message_count"),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )

    query = (
        db.query(
            Conversation,
            func.coalesce(activity_subq.c.last_message_at, Conversation.created_at).label("last_message_at"),
            func.coalesce(activity_subq.c.message_count, 0).label("message_count"),
        )
        .outerjoin(activity_subq, activity_subq.c.conversation_id == Conversation.id)
        .filter(Conversation.user_id == user_id)
    )

    if agent_slug:
        query = query.filter(Conversation.agent_slug == agent_slug)
    if channel:
        query = query.filter(Conversation.channel == channel)
    if group_id and group_id.lower() in ("null", "none"):
        query = query.filter(Conversation.group_id.is_(None))
    elif group_id:
        _resolve_owned_group(db, owner_id=user_id, group_id=group_id)
        query = query.filter(Conversation.group_id == group_id)
    elif without_group:
        query = query.filter(Conversation.group_id.is_(None))

    total = query.count()
    rows = (
        query.order_by(
            func.coalesce(activity_subq.c.last_message_at, Conversation.created_at).desc(),
            Conversation.created_at.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = [
        ConversationSummary(
            conversation_id=conv.external_id or conv.id,
            name=conv.name,
            external_id=conv.external_id,
            channel=conv.channel,
            agent_slug=conv.agent_slug,
            group_id=conv.group_id,
            created_at=conv.created_at,
            last_message_at=last_message_at,
            message_count=int(message_count or 0),
        )
        for conv, last_message_at, message_count in rows
    ]

    return ConversationsResponse(
        items=items,
        pagination=PaginationInfo(
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + len(items)) < total,
        ),
    )


@router.put("/user-metadata", response_model=UserMetadataResponse)
async def upsert_user_metadata(
    req: UserMetadataUpsertRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_auth()),
):
    owner_id = _require_user_id(principal)

    item = (
        db.query(UserMetadata)
        .filter(UserMetadata.owner_id == owner_id)
        .first()
    )
    if not item:
        item = UserMetadata(owner_id=owner_id, metadata_json=req.metadata or {})
    else:
        item.metadata_json = req.metadata or {}

    db.add(item)
    db.commit()
    db.refresh(item)
    return _user_metadata_summary(item)


@router.get("/user-metadata", response_model=UserMetadataResponse)
async def get_user_metadata(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_auth()),
):
    owner_id = _require_user_id(principal)

    item = (
        db.query(UserMetadata)
        .filter(UserMetadata.owner_id == owner_id)
        .first()
    )
    if not item:
        item = UserMetadata(owner_id=owner_id, metadata_json={})
        db.add(item)
        db.commit()
        db.refresh(item)
    return _user_metadata_summary(item)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationMessagesResponse,
)
async def list_conversation_messages(
    conversation_id: str,
    role: str | None = Query(default=None),
    include_tools: bool = Query(default=False),
    channel: str | None = Query(default=None),
    agent_slug: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_auth()),
):
    user_id = _require_user_id(principal)
    conv = _resolve_user_conversation(
        db,
        user_id=user_id,
        conversation_identifier=conversation_id,
        channel=channel,
        agent_slug=agent_slug,
    )
    if not conv:
        raise HTTPException(status_code=404, detail="conversation_not_found")

    query = db.query(Message).filter(Message.conversation_id == conv.id)

    if role:
        try:
            selected_role = Role(role)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid_role_filter") from exc
        query = query.filter(Message.role == selected_role)
    elif not include_tools:
        query = query.filter(Message.role.in_([Role.user, Role.assistant]))
        query = query.filter(Message.tool_name.is_(None))

    total = query.count()
    rows = (
        query.order_by(Message.created_at.desc(), Message.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = [
        ConversationMessage(
            id=msg.id,
            role=msg.role.value,
            content=msg.content,
            created_at=msg.created_at,
            tool_name=msg.tool_name,
        )
        for msg in rows
    ]

    return ConversationMessagesResponse(
        conversation_id=conv.external_id or conv.id,
        items=items,
        pagination=PaginationInfo(
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + len(items)) < total,
        ),
    )

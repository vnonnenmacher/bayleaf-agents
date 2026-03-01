import inspect

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth.deps import Principal, require_auth
from ..db import get_db
from ..models import Conversation, Message, Role
from ..schemas.chat import (
    ChatRequest,
    ChatResponse,
    ConversationMessage,
    ConversationMessagesResponse,
    ConversationsResponse,
    ConversationSummary,
    PaginationInfo,
    SafetyInfo,
)
from ..services.agent_registry import discover_agents
from ..services.factories import (
    get_bayleaf,
    get_decider_provider,
    get_documents_tools,
    get_phi_filter,
    get_provider,
)

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
    conv = q.filter(Conversation.external_id == conversation_identifier).first()
    if conv:
        return conv
    return q.filter(Conversation.id == conversation_identifier).first()


for slug, AgentCls in _AGENT_CLASSES.items():

    async def chat_endpoint(
        req: ChatRequest,
        db: Session = Depends(get_db),
        principal: Principal = Depends(require_auth()),
        _AgentCls=AgentCls,
        _slug=slug,
    ):
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
        result = agent.chat(
            db=db,
            channel=req.channel,
            user_message=req.message,
            external_conversation_id=req.conversation_id,
            principal=principal,  # token goes through; server infers patient
            lang=req.lang or "pt-BR",
            agent_slug=_slug,
        )
        safety = SafetyInfo(triage="non-urgent")
        return ChatResponse(
            reply=result["reply"],
            used_tools=result["used_tools"],
            safety=safety,
            trace_id=result["trace_id"],
            conversation_id=result["conversation_id"],
        )

    router.add_api_route(
        f"/{slug}/chat",
        chat_endpoint,
        methods=["POST"],
        response_model=ChatResponse,
        name=f"{slug}-chat",
    )


@router.get("/conversations", response_model=ConversationsResponse)
async def list_conversations(
    agent_slug: str | None = Query(default=None),
    channel: str | None = Query(default=None),
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
            external_id=conv.external_id,
            channel=conv.channel,
            agent_slug=conv.agent_slug,
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

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..db import get_db
from ..schemas.chat import ChatRequest, ChatResponse, SafetyInfo
from ..services.agent_registry import discover_agents
from ..services.factories import get_provider, get_bayleaf, get_phi_filter
from ..auth.deps import require_auth, Principal

router = APIRouter(prefix="/agents", tags=["agents"])
_AGENT_CLASSES = discover_agents()

for slug, AgentCls in _AGENT_CLASSES.items():

    async def chat_endpoint(
        req: ChatRequest,
        db: Session = Depends(get_db),
        principal: Principal = Depends(require_auth()),
        _AgentCls=AgentCls,
    ):
        agent = _AgentCls(provider=get_provider(), bayleaf=get_bayleaf(), phi_filter=get_phi_filter())
        result = agent.chat(
            db=db,
            channel=req.channel,
            user_message=req.message,
            external_conversation_id=req.conversation_id,
            principal=principal,  # token goes through; server infers patient
            lang=req.lang or "pt-BR",
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

import inspect

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth.deps import Principal, require_auth
from ..db import get_db
from ..schemas.chat import ChatRequest, ChatResponse, SafetyInfo
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


for slug, AgentCls in _AGENT_CLASSES.items():

    async def chat_endpoint(
        req: ChatRequest,
        db: Session = Depends(get_db),
        principal: Principal = Depends(require_auth()),
        _AgentCls=AgentCls,
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

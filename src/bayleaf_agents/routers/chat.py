from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..schemas.chat import ChatRequest, ChatResponse, SafetyInfo
from ..services.agent import run_chat
from ..db import get_db


router = APIRouter(tags=["chat"])


async def _auth():  # stub
    return {"sub": "dev-user"}

@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, db: Session = Depends(get_db), _user=Depends(_auth)):
    result = run_chat(
        db,
        channel=req.channel,
        patient_id=req.patient_id,
        user_message=req.message,
        external_conversation_id=req.conversation_id,
    )
    safety = SafetyInfo(triage="non-urgent")
    return ChatResponse(
        reply=result["reply"],
        used_tools=result["used_tools"],
        safety=safety,
        trace_id=result["trace_id"],
        conversation_id=result["conversation_id"],
    )

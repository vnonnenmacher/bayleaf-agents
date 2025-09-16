from fastapi import APIRouter, Depends
from ..schemas.chat import ChatRequest, ChatResponse, SafetyInfo
from ..services.agent import run_chat

router = APIRouter(tags=["chat"])


# Minimal auth stub – wire real JWT later
async def _auth():
    return {"sub": "dev-user"}


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, _user=Depends(_auth)):
    result = run_chat(patient_id=req.patient_id, user_message=req.message)
    # simple safety placeholder
    safety = SafetyInfo(triage="non-urgent")
    return ChatResponse(
        reply=result["reply"],
        used_tools=result["used_tools"],
        safety=safety,
        trace_id=result["trace_id"],
    )

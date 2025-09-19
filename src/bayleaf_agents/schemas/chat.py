# src/bayleaf_agents/schemas/chat.py
from typing import Optional, Literal
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    channel: Literal['bayleaf_app','whatsapp','partner']
    message: str = Field(min_length=1)
    conversation_id: Optional[str] = None
    lang: Optional[str] = None


class SafetyInfo(BaseModel):
    triage: Literal['non-urgent','urgent','emergency'] = 'non-urgent'


class ChatResponse(BaseModel):
    reply: str
    used_tools: list[str]
    safety: SafetyInfo
    trace_id: str
    conversation_id: str

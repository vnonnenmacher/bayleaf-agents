from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from .common import Channel


class ChatRequest(BaseModel):
    channel: Channel
    patient_id: str
    message: str
    locale: str = "pt-BR"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SafetyInfo(BaseModel):
    triage: str = "unknown"
    advice: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    used_tools: List[str] = Field(default_factory=list)
    safety: SafetyInfo = Field(default_factory=SafetyInfo)
    trace_id: str

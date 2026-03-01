# src/bayleaf_agents/schemas/chat.py
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    channel: Literal['bayleaf_app', 'whatsapp', 'partner']
    message: str = Field(min_length=1)
    conversation_id: Optional[str] = None
    lang: Optional[str] = None


class SafetyInfo(BaseModel):
    triage: Literal['non-urgent', 'urgent', 'emergency'] = 'non-urgent'


class ChatResponse(BaseModel):
    reply: str
    used_tools: list[str]
    safety: SafetyInfo
    trace_id: str
    conversation_id: str


class PaginationInfo(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool


class ConversationSummary(BaseModel):
    conversation_id: str
    external_id: Optional[str] = None
    channel: str
    agent_slug: Optional[str] = None
    created_at: datetime
    last_message_at: datetime
    message_count: int


class ConversationsResponse(BaseModel):
    items: list[ConversationSummary]
    pagination: PaginationInfo


class ConversationMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    tool_name: Optional[str] = None


class ConversationMessagesResponse(BaseModel):
    conversation_id: str
    items: list[ConversationMessage]
    pagination: PaginationInfo

# src/bayleaf_agents/schemas/chat.py
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    channel: Literal['bayleaf_app', 'whatsapp', 'partner']
    message: str = Field(min_length=1)
    conversation_id: Optional[str] = None
    group_id: Optional[str] = None
    document_uuids: Optional[list[str]] = None
    lang: Optional[str] = None


class SafetyInfo(BaseModel):
    triage: Literal['non-urgent', 'urgent', 'emergency'] = 'non-urgent'


class ResearchDocument(BaseModel):
    name: str
    uuid: str


class ChatResponse(BaseModel):
    reply: str
    used_tools: list[str]
    research_documents: list[ResearchDocument] = Field(default_factory=list)
    safety: SafetyInfo
    trace_id: str
    conversation_id: str
    conversation_name: str


class PaginationInfo(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool


class ConversationSummary(BaseModel):
    conversation_id: str
    name: str
    external_id: Optional[str] = None
    channel: str
    agent_slug: Optional[str] = None
    group_id: Optional[str] = None
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


class ConversationGroupCreateRequest(BaseModel):
    type: Literal["project", "event"]
    metadata: dict = Field(default_factory=dict)
    document_uuids: list[str] = Field(default_factory=list)
    is_active: bool = True


class ConversationGroupUpdateRequest(BaseModel):
    is_active: Optional[bool] = None
    metadata: Optional[dict] = None
    document_uuids: Optional[list[str]] = None


class ConversationGroupSummary(BaseModel):
    id: str
    owner_id: str
    type: Literal["project", "event"]
    is_active: bool
    metadata: dict
    document_uuids: list[str]
    created_at: datetime
    updated_at: datetime


class ConversationGroupsResponse(BaseModel):
    items: list[ConversationGroupSummary]
    pagination: PaginationInfo

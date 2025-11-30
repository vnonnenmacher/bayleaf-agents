import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, ForeignKey, Enum, JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base
import enum


class Role(str, enum.Enum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    external_id: Mapped[Optional[str]] = mapped_column(
        String(100), index=True
    )  # client-provided (conversation_id)

    # replaced patient_id with user_id
    user_id: Mapped[str] = mapped_column(String(100), index=True)

    channel: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all,delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[Role] = mapped_column(Enum(Role))
    content: Mapped[str] = mapped_column(Text)
    redacted_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tool_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tool_args: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    tool_result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class PHIEntity(Base):
    __tablename__ = "phi_entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    placeholder: Mapped[str] = mapped_column(String(80))
    original_text: Mapped[str] = mapped_column(Text)
    start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

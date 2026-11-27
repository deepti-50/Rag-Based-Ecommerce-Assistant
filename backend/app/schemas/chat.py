"""Request/response shapes for the shopping assistant."""
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = Field(default=None, description="Omit to start a new conversation")


class ToolCall(BaseModel):
    name: str
    args: dict[str, Any]


class ChatResponse(BaseModel):
    conversation_id: uuid.UUID
    answer: str
    tools_used: list[ToolCall] = Field(description="What the agent looked up to answer (for transparency/debugging)")


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    created_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None
    created_at: datetime

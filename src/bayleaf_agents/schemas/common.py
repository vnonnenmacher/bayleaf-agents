from pydantic import BaseModel, Field
from typing import Any, Dict, Literal

Channel = Literal["bayleaf_app", "whatsapp", "partner"]


class ToolCall(BaseModel):
    name: str
    args: Dict[str, Any] = Field(default_factory=dict)

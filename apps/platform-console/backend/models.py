"""Request models and the response envelope.

Finding rows deliberately stay list[dict]: they originate in dbx_platform's
fetch/classify functions, and re-modeling each row shape here would create a
second schema that drifts from the package. Only payloads the app itself
composes get typed models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def envelope(data: Any, as_of: datetime, was_cached: bool) -> dict:
    return {
        "data": data,
        "count": len(data) if isinstance(data, list) else None,
        "as_of": as_of.isoformat(),
        "cached": was_cached,
    }


class ApplyRequest(BaseModel):
    plan_id: str
    confirm: str


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)

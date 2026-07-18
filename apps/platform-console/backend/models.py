"""Request models and the response envelope.

Finding rows deliberately stay list[dict]: they originate in dbx_platform's
fetch/classify functions, and re-modeling each row shape here would create a
second schema that drifts from the package. Only payloads the app itself
composes get typed models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, model_validator


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


class ActionPlanRequest(BaseModel):
    action_type: str = Field(
        min_length=1,
        max_length=100,
        validation_alias=AliasChoices("action_type", "action"),
    )
    parameters: dict[str, Any] = Field(default_factory=dict)


class ActionApprovalRequest(BaseModel):
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: str | None = Field(
        default=None,
        validation_alias=AliasChoices("confirmation", "confirm"),
    )


class ActionRejectRequest(BaseModel):
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, max_length=1000)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class ChatPageContext(BaseModel):
    """Bounded display context; never valid as an executor/tool payload."""

    route: str = Field(default="/", min_length=1, max_length=200, pattern=r"^/")
    query: str = Field(default="", max_length=1000)
    filters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    selected_resources: list[dict[str, str]] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def bounded_context(self):
        if len(self.filters) > 30:
            raise ValueError("Assistant context accepts at most 30 filters.")
        if any(len(str(key)) > 100 for key in self.filters):
            raise ValueError("Assistant context filter names are too long.")
        for resource in self.selected_resources:
            if len(resource) > 10 or any(
                len(str(key)) > 100 or len(str(value)) > 500
                for key, value in resource.items()
            ):
                raise ValueError("Assistant selected-resource context is too large.")
        return self


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=50)
    context: ChatPageContext = Field(default_factory=ChatPageContext)

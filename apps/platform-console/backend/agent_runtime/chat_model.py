"""LangChain chat-model adapter for App-bound Databricks serving endpoints."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, convert_to_messages
from langchain_core.messages.utils import convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field


class DatabricksChatModel(BaseChatModel):
    """Small LangChain adapter over the App's existing Databricks SDK client.

    The full ``databricks-langchain`` distribution pulls MLflow, AI Search,
    MCP, OpenAI Agents, Unity Catalog adapters, and scientific Python packages.
    Mission Control needs only foundation-model chat with tool calling, which
    the serving endpoint's OpenAI-compatible invocation API already provides.
    """

    endpoint: str
    workspace_client: Any = Field(exclude=True)
    temperature: float = 0.1
    max_tokens: int = 1200

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return "databricks-serving-endpoint"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"endpoint": self.endpoint}

    def bind_tools(
        self,
        tools,
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ):
        formatted = [convert_to_openai_tool(candidate) for candidate in tools]
        if tool_choice == "any":
            tool_choice = "required"
        elif tool_choice not in {None, "auto", "none", "required"}:
            tool_choice = {
                "type": "function",
                "function": {"name": str(tool_choice)},
            }
        return self.bind(
            tools=formatted,
            tool_choice=tool_choice,
            **kwargs,
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager=None,
        **kwargs: Any,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "messages": convert_to_openai_messages(messages),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if stop:
            payload["stop"] = stop
        for key in ("tools", "tool_choice"):
            if kwargs.get(key) is not None:
                payload[key] = kwargs[key]

        response = self.workspace_client.api_client.do(
            "POST",
            (
                "/api/2.0/serving-endpoints/"
                f"{quote(self.endpoint, safe='')}/invocations"
            ),
            body=payload,
        )
        choices = response.get("choices") if isinstance(response, dict) else None
        if not choices or not isinstance(choices[0], dict):
            raise ValueError("Databricks serving endpoint returned no chat choices.")
        raw_message = choices[0].get("message")
        if not isinstance(raw_message, dict):
            raise ValueError("Databricks serving endpoint returned no assistant message.")
        message = convert_to_messages([raw_message])[0]
        if not isinstance(message, AIMessage):
            raise ValueError("Databricks serving endpoint returned a non-assistant message.")
        return ChatResult(generations=[ChatGeneration(message=message)])

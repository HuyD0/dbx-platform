"""LangGraph agent hosted inside the Platform Console FastAPI process.

The graph can call only the read-only tools assembled here. Its model is the
App's CAN_QUERY-bound foundation-model endpoint; it is not itself deployed as
a separate model-serving endpoint.
"""

from __future__ import annotations

import json
import os
from functools import cached_property
from typing import Any
from urllib.parse import quote

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, convert_to_messages
from langchain_core.messages.utils import convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field

from dbx_platform.platform_agent import tools as shared_tools
from dbx_platform.platform_agent.formatting import SYSTEM_PROMPT, rows_to_text


def _text_content(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
        return "\n".join(chunks).strip()
    return str(content or "").strip()


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


def _configure_mlflow_tracing(experiment_id: str) -> None:
    """Enable production LangGraph tracing against the App-bound experiment."""

    if not experiment_id:
        raise RuntimeError(
            "The App-bound MLflow trace experiment is not configured."
        )
    import mlflow
    import mlflow.langchain

    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment(experiment_id=experiment_id)
    mlflow.langchain.autolog(log_traces=True, silent=True)


class PlatformAgent:
    """Lazy, process-local LangGraph runtime with App-safe tool bindings."""

    def __init__(
        self,
        *,
        endpoint: str,
        workspace_client_factory,
        settings_factory,
        repository_factory,
    ) -> None:
        self.endpoint = endpoint
        self.workspace_client_factory = workspace_client_factory
        self.settings_factory = settings_factory
        self.repository_factory = repository_factory

    def _canonical_findings_tool(self):
        repository_factory = self.repository_factory

        @tool
        def get_canonical_findings(
            pillar: str | None = None,
            state: str | None = "OPEN",
            limit: int = 50,
        ) -> str:
            """Read normalized Mission Control findings.

            Use for security, governance, housekeeping, serving, runtime, and
            other scheduled evidence. Pillar values include COST, SECURITY,
            RELIABILITY, PERFORMANCE, GOVERNANCE, and RISK.
            """
            rows = repository_factory().list_findings(
                pillar=pillar.upper() if pillar else None,
                state=state.upper() if state else None,
                limit=max(1, min(limit, 100)),
            )
            safe_rows = [
                {
                    "finding_id": row.get("finding_id"),
                    "pillar": row.get("pillar"),
                    "severity": row.get("severity"),
                    "likelihood": row.get("likelihood"),
                    "financial_impact_usd": row.get("financial_impact_usd"),
                    "slo_impact": row.get("slo_impact"),
                    "confidence": row.get("confidence"),
                    "resource_types": sorted({
                        str(resource.get("resource_type"))
                        for resource in (row.get("affected_resources") or [])
                        if isinstance(resource, dict) and resource.get("resource_type")
                    }),
                    "freshness_at": row.get("freshness_at"),
                    "state": row.get("state"),
                    "proposed_action_type": row.get("proposed_action_type"),
                    "blast_radius": row.get("blast_radius"),
                    "check_name": row.get("check_name"),
                }
                for row in rows
            ]
            return rows_to_text(
                safe_rows,
                tool_name="get_canonical_findings",
                source="canonical platform_findings",
            )

        return get_canonical_findings

    def _proposal_tool(self):
        repository_factory = self.repository_factory

        @tool
        def propose_remediation(action: str) -> str:
            """Draft a Mission Control remediation proposal from open findings.

            Valid actions are the proposed_action_type values present in
            canonical findings. This changes nothing; copy the ACTION_PROPOSAL
            marker verbatim into the final answer.
            """
            rows = repository_factory().list_findings(state="OPEN", limit=1000)
            matches = [
                row for row in rows
                if str(row.get("proposed_action_type") or row.get("action") or "")
                == action
            ]
            if not matches:
                return f"No open canonical findings support action '{action}'."
            marker = json.dumps(
                {"action": action, "count": len(matches)},
                sort_keys=True,
                separators=(",", ":"),
            )
            return (
                rows_to_text(
                    [
                        {
                            "finding_id": row.get("finding_id"),
                            "severity": row.get("severity"),
                            "freshness_at": row.get("freshness_at"),
                            "proposed_action_type": action,
                        }
                        for row in matches[:50]
                    ],
                    tool_name="propose_remediation",
                    source="canonical platform_findings dry-run",
                )
                + f"\nACTION_PROPOSAL:{marker}"
            )

        return propose_remediation

    @cached_property
    def graph(self):
        from langgraph.prebuilt import create_react_agent

        experiment_id = os.environ.get("MLFLOW_EXPERIMENT_ID", "").strip()
        _configure_mlflow_tracing(experiment_id)

        shared_tools.configure_runtime(
            client_factory=self.workspace_client_factory,
            settings_factory=self.settings_factory,
        )
        llm = DatabricksChatModel(
            endpoint=self.endpoint,
            workspace_client=self.workspace_client_factory(),
            temperature=0.1,
            max_tokens=1200,
        )
        tools = [
            shared_tools.get_cost_report,
            shared_tools.get_top_jobs,
            shared_tools.get_cluster_utilization,
            shared_tools.get_failed_run_waste,
            shared_tools.get_llm_cost_and_efficiency,
            shared_tools.get_warehouse_utilization,
            self._canonical_findings_tool(),
            self._proposal_tool(),
        ]
        return create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)

    def invoke(self, messages: list[dict[str, str]]) -> str:
        result = self.graph.invoke(
            {"messages": messages},
            config={"recursion_limit": 20},
        )
        output = result.get("messages") if isinstance(result, dict) else None
        if not output:
            return ""
        return _text_content(output[-1])

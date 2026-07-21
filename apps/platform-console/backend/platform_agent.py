"""LangGraph agent hosted inside the Platform Console FastAPI process.

The graph can call only the read-only tools assembled here. Its model is the
App's CAN_QUERY-bound foundation-model endpoint; it is not itself deployed as
a separate model-serving endpoint.
"""

from __future__ import annotations

import json
import os
import time
from functools import cached_property
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import tool

from backend.agent_runtime import DatabricksChatModel, configure_mlflow_tracing
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


class _ExecutionTimingHandler(BaseCallbackHandler):
    """Collect bounded server timings without logging prompts or tool output."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.active: dict[str, tuple[str, str, float]] = {}
        self.stages: list[dict[str, Any]] = []

    def _start(self, run_id: object, kind: str, label: str) -> None:
        self.active[str(run_id)] = (kind, label[:200], time.perf_counter())

    def _end(self, run_id: object) -> None:
        active = self.active.pop(str(run_id), None)
        if active is None or len(self.stages) >= 50:
            return
        kind, label, started = active
        self.stages.append(
            {
                "id": f"stage-{len(self.stages) + 1}",
                "label": label,
                "category": kind,
                "start_ms": round((started - self.started) * 1000, 1),
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs) -> None:
        del serialized, prompts, kwargs
        self._start(run_id, "llm_synthesis", "LLM response synthesis")

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        del serialized, messages, kwargs
        self._start(run_id, "llm_synthesis", "LLM response synthesis")

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        del response, kwargs
        self._end(run_id)

    def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        del error, kwargs
        self._end(run_id)

    def on_tool_start(
        self,
        serialized,
        input_str,
        *,
        run_id,
        name: str | None = None,
        **kwargs,
    ) -> None:
        del input_str, kwargs
        tool_name = name or (serialized or {}).get("name") or "evidence tool"
        is_foundry_tool = any(
            marker in str(tool_name).lower()
            for marker in ("foundry", "azure_ai", "azure_openai")
        )
        self._start(
            run_id,
            "foundry_agent" if is_foundry_tool else "databricks_retrieval",
            (
                f"Microsoft Foundry Agent · {tool_name}"
                if is_foundry_tool
                else f"Databricks retrieval · {tool_name}"
            ),
        )

    def on_tool_end(self, output, *, run_id, **kwargs) -> None:
        del output, kwargs
        self._end(run_id)

    def on_tool_error(self, error, *, run_id, **kwargs) -> None:
        del error, kwargs
        self._end(run_id)

    def trace(self) -> dict[str, Any]:
        total_ms = round((time.perf_counter() - self.started) * 1000, 1)
        stages = sorted(self.stages, key=lambda stage: stage["start_ms"])
        if not stages:
            stages = [
                {
                    "id": "stage-1",
                    "label": "LLM response synthesis",
                    "category": "llm_synthesis",
                    "start_ms": 0,
                    "duration_ms": total_ms,
                    "detail": "Only end-to-end server timing was available.",
                }
            ]
        return {
            "total_ms": total_ms,
            # The adapter is deliberately non-streaming, so reporting TTFT or
            # time-per-token would manufacture precision that is not observed.
            "ttft_ms": None,
            "tpot_ms": None,
            "timing_source": "server",
            "stages": stages,
        }


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

    supports_execution_trace = True

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
        if experiment_id:
            configure_mlflow_tracing(experiment_id)

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
            shared_tools.list_solution_patterns,
            shared_tools.estimate_solution_cost,
            self._canonical_findings_tool(),
            self._proposal_tool(),
        ]
        return create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)

    def invoke_with_trace(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        timing = _ExecutionTimingHandler()
        result = self.graph.invoke(
            {"messages": messages},
            config={"recursion_limit": 20, "callbacks": [timing]},
        )
        output = result.get("messages") if isinstance(result, dict) else None
        if not output:
            return "", timing.trace()
        return _text_content(output[-1]), timing.trace()

    def invoke(self, messages: list[dict[str, str]]) -> str:
        text, _trace = self.invoke_with_trace(messages)
        return text

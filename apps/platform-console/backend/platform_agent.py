"""LangGraph agent hosted inside the Platform Console FastAPI process."""

from __future__ import annotations

import json
from functools import cached_property

from langchain_core.tools import tool

from dbx_platform.platform_agent import tools
from dbx_platform.platform_agent.formatting import SYSTEM_PROMPT, rows_to_text


def _text_content(message) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            part if isinstance(part, str) else str(part.get("text", ""))
            for part in content
            if isinstance(part, str) or isinstance(part, dict)
        ).strip()
    return str(content or "").strip()


class PlatformAgent:
    """Lazy local graph whose only capabilities are read-only package tools."""

    def __init__(
        self,
        *,
        endpoint,
        workspace_client_factory,
        settings_factory,
        repository_factory,
    ):
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
            """Read normalized Mission Control findings ranked by severity and impact."""
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
                    "financial_impact_usd": row.get("financial_impact_usd"),
                    "slo_impact": row.get("slo_impact"),
                    "confidence": row.get("confidence"),
                    "resource_types": sorted({
                        str(resource.get("resource_type"))
                        for resource in (row.get("affected_resources") or [])
                        if isinstance(resource, dict) and resource.get("resource_type")
                    }),
                    "freshness_at": row.get("freshness_at"),
                    "proposed_action_type": row.get("proposed_action_type"),
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
            """Draft a proposal supported by open canonical findings; changes nothing."""
            rows = repository_factory().list_findings(state="OPEN", limit=1000)
            matches = [
                row
                for row in rows
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
            evidence = rows_to_text(
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
            return f"{evidence}\nACTION_PROPOSAL:{marker}"

        return propose_remediation

    @cached_property
    def graph(self):
        from databricks_langchain import ChatDatabricks
        from langgraph.prebuilt import create_react_agent

        tools.configure_runtime(
            client_factory=self.workspace_client_factory,
            settings_factory=self.settings_factory,
        )
        model = ChatDatabricks(
            endpoint=self.endpoint,
            workspace_client=self.workspace_client_factory(),
            temperature=0.1,
            max_tokens=1200,
        )
        read_only_tools = [
            *tools.ALL_TOOLS,
            self._canonical_findings_tool(),
            self._proposal_tool(),
        ]
        return create_react_agent(model, read_only_tools, prompt=SYSTEM_PROMPT)

    def invoke(self, messages: list[dict[str, str]]) -> str:
        result = self.graph.invoke({"messages": messages}, config={"recursion_limit": 20})
        output = result.get("messages") if isinstance(result, dict) else None
        return _text_content(output[-1]) if output else ""

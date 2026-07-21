"""MLflow ResponsesAgent wrapper around the packaged read-only LangGraph graph.

The graph, tools and prompt live in the ``dbx_platform.agent`` package (shipped
in the wheel) so the Platform Console can run them in-process. This module only
adapts that graph to the Mosaic AI Agent Framework for the *optional* served
deployment. Serving is still gated: ``deploy_agent.py`` intentionally refuses
to register or deploy until a narrowly scoped, approved model-deploy action
exists (see docs/runbook.md).

Logged code-based (mlflow.models.set_model at the bottom).
"""

from __future__ import annotations

import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

from dbx_platform.agent.graph import build_graph


class PlatformResponsesAgent(ResponsesAgent):
    """Minimal ResponsesAgent adapter around the LangGraph graph."""

    def __init__(self):
        self._graph = None

    @property
    def graph(self):
        if self._graph is None:
            self._graph = build_graph()
        return self._graph

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        messages = [
            {"role": i.role, "content": i.content} for i in request.input
        ]
        result = self.graph.invoke({"messages": messages})
        final = result["messages"][-1]
        item = self.create_text_output_item(
            text=final.content if isinstance(final.content, str) else str(final.content),
            id=getattr(final, "id", None) or "output-0",
        )
        return ResponsesAgentResponse(output=[item])


AGENT = PlatformResponsesAgent()
mlflow.models.set_model(AGENT)

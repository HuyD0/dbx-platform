"""LangGraph ReAct agent over the dbx-platform read-only tools, wrapped as an
MLflow ResponsesAgent for the Mosaic AI Agent Framework.

Logged code-based (mlflow.models.set_model at the bottom); served on a
Databricks model serving endpoint by deploy_agent.py. The LLM is a
Databricks-hosted foundation model (same endpoint setting as the digest).
"""

from __future__ import annotations

import os

import mlflow
from databricks_langchain import ChatDatabricks
from langgraph.prebuilt import create_react_agent
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

from dbx_platform.config import Settings

# Package-relative imports are unavailable when MLflow loads this file as a
# standalone model; both layouts are supported.
try:
    from .formatting import SYSTEM_PROMPT
    from .tools import ALL_TOOLS
except ImportError:  # pragma: no cover - serving layout
    from formatting import SYSTEM_PROMPT
    from tools import ALL_TOOLS


def build_graph():
    model_endpoint = (
        os.environ.get("DBX_PLATFORM_DIGEST_MODEL") or Settings().digest_model
    )
    llm = ChatDatabricks(endpoint=model_endpoint)
    return create_react_agent(llm, ALL_TOOLS, prompt=SYSTEM_PROMPT)


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

"""The dbx-platform read-only chat agent — packaged in the wheel.

The agent's LangGraph graph and its LangChain tool wrappers live here so both
surfaces can reuse the exact same read-only logic:

- the Platform Console runs the graph **in-process** (apps/platform-console),
  after installing the ``dbx-platform[chat]`` extra; and
- a future, governed model-deploy action can serve the same graph on a model
  serving endpoint (``agents/platform_agent`` wraps it as an MLflow
  ResponsesAgent).

``formatting`` is import-light (no third-party deps) and is loaded directly by
tests. ``tools`` and ``graph`` pull in ``langchain``/``langgraph`` and must
therefore only be imported lazily, inside a request path, by callers that have
the ``chat`` extra installed. This package's ``__init__`` intentionally imports
nothing heavy so ``import dbx_platform.agent.formatting`` stays cheap.
"""

from __future__ import annotations

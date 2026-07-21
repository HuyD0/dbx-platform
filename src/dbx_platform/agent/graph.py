"""Build the read-only LangGraph ReAct agent.

Imports ``databricks_langchain`` and ``langgraph`` (the ``chat``/``agent``
extra). Callers that only have the core wheel must not import this module; the
Platform Console imports it lazily, inside the chat request path, after the
``chat`` extra is installed.

The LLM is a Databricks-hosted foundation model. In the app it is the endpoint
bound as the ``chat-model`` app resource and exposed via
``DBX_PLATFORM_CHAT_MODEL_ENDPOINT``; otherwise it falls back to the same model
the digest uses (``Settings().digest_model``).
"""

from __future__ import annotations

import os

from databricks_langchain import ChatDatabricks
from langgraph.prebuilt import create_react_agent

from dbx_platform.config import Settings

from .formatting import SYSTEM_PROMPT
from .tools import ALL_TOOLS


def model_endpoint() -> str:
    """Serving-endpoint name of the foundation model the agent reasons with."""
    return (
        os.environ.get("DBX_PLATFORM_CHAT_MODEL_ENDPOINT")
        or os.environ.get("DBX_PLATFORM_DIGEST_MODEL")
        or Settings().digest_model
    )


def build_graph():
    """Compile the read-only ReAct graph over ALL_TOOLS. Ambient auth."""
    llm = ChatDatabricks(endpoint=model_endpoint())
    return create_react_agent(llm, ALL_TOOLS, prompt=SYSTEM_PROMPT)

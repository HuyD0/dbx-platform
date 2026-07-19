from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from backend.platform_agent import (  # noqa: E402
    DatabricksChatModel,
    _configure_mlflow_tracing,
)
from langchain_core.messages import (  # noqa: E402
    AIMessage,
    HumanMessage,
    ToolMessage,
)


class DatabricksChatModelTests(unittest.TestCase):
    def test_invokes_bound_endpoint_with_openai_tool_messages(self) -> None:
        workspace = MagicMock()
        workspace.api_client.do.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "get_cost_report",
                                    "arguments": '{"days": 7}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
        model = DatabricksChatModel(
            endpoint="foundation/model",
            workspace_client=workspace,
        )
        messages = [
            HumanMessage(content="Show cost."),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_cost_report",
                        "args": {"days": 7},
                        "id": "prior-call",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(content="USD 12", tool_call_id="prior-call"),
        ]

        result = model._generate(
            messages,
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_cost_report",
                        "description": "Read costs",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )

        message = result.generations[0].message
        self.assertEqual(message.tool_calls[0]["name"], "get_cost_report")
        self.assertEqual(message.tool_calls[0]["args"], {"days": 7})
        workspace.api_client.do.assert_called_once()
        method, path = workspace.api_client.do.call_args.args
        payload = workspace.api_client.do.call_args.kwargs["body"]
        self.assertEqual(method, "POST")
        self.assertEqual(
            path,
            "/api/2.0/serving-endpoints/foundation%2Fmodel/invocations",
        )
        self.assertEqual(payload["messages"][2]["role"], "tool")
        self.assertEqual(payload["messages"][2]["tool_call_id"], "prior-call")
        self.assertEqual(payload["tools"][0]["function"]["name"], "get_cost_report")

    def test_rejects_a_response_without_an_assistant_choice(self) -> None:
        workspace = MagicMock()
        workspace.api_client.do.return_value = {"choices": []}
        model = DatabricksChatModel(endpoint="model", workspace_client=workspace)

        with self.assertRaisesRegex(ValueError, "no chat choices"):
            model._generate([HumanMessage(content="hello")])

    def test_configures_langgraph_tracing_for_bound_experiment(self) -> None:
        with (
            patch("mlflow.set_tracking_uri") as set_tracking_uri,
            patch("mlflow.set_experiment") as set_experiment,
            patch("mlflow.langchain.autolog") as autolog,
        ):
            _configure_mlflow_tracing("experiment-123")

        set_tracking_uri.assert_called_once_with("databricks")
        set_experiment.assert_called_once_with(experiment_id="experiment-123")
        autolog.assert_called_once_with(log_traces=True, silent=True)

    def test_trace_experiment_is_required(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "trace experiment"):
            _configure_mlflow_tracing("")


if __name__ == "__main__":
    unittest.main()

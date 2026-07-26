from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from backend.agent_runtime.chat_model import DatabricksChatModel  # noqa: E402
from backend.agent_runtime.tracing import (  # noqa: E402
    configure_mlflow_tracing,
    mlflow_span,
)
from backend.platform_agent import PlatformAgent  # noqa: E402
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
            "/serving-endpoints/foundation%2Fmodel/invocations",
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

    def test_configures_native_tracing_for_bound_experiment(self) -> None:
        with (
            patch("mlflow.set_tracking_uri") as set_tracking_uri,
            patch("mlflow.tracing.set_destination") as set_destination,
        ):
            enabled = configure_mlflow_tracing("experiment-123")

        self.assertTrue(enabled)
        set_tracking_uri.assert_called_once_with("databricks")
        destination = set_destination.call_args.args[0]
        self.assertEqual(destination.experiment_id, "experiment-123")

    def test_tracing_failure_does_not_block_the_agent(self) -> None:
        with (
            patch("mlflow.set_tracking_uri"),
            patch(
                "mlflow.tracing.set_destination",
                side_effect=RuntimeError("trace service unavailable"),
            ),
            self.assertLogs(
                "backend.agent_runtime.tracing",
                level="WARNING",
            ) as captured,
        ):
            enabled = configure_mlflow_tracing("experiment-123")

        self.assertFalse(enabled)
        self.assertIn(
            "continuing without tracing",
            "\n".join(captured.output),
        )

    def test_native_span_records_inputs_and_finalizes(self) -> None:
        manager = MagicMock()
        span = manager.__enter__.return_value
        with patch("mlflow.start_span", return_value=manager) as start_span:
            with mlflow_span(
                "platform_agent.invoke",
                span_type="AGENT",
                inputs={"messages": [{"role": "user", "content": "hello"}]},
                enabled=True,
            ) as active_span:
                self.assertIs(active_span, span)

        start_span.assert_called_once_with(
            name="platform_agent.invoke",
            span_type="AGENT",
        )
        span.set_inputs.assert_called_once()
        manager.__exit__.assert_called_once_with(None, None, None)

    def test_native_span_start_failure_is_fail_open(self) -> None:
        with (
            patch("mlflow.start_span", side_effect=RuntimeError("trace service unavailable")),
            self.assertLogs(
                "backend.agent_runtime.tracing",
                level="WARNING",
            ),
        ):
            with mlflow_span(
                "platform_agent.invoke",
                span_type="AGENT",
                inputs={},
                enabled=True,
            ) as span:
                self.assertIsNone(span)

    def test_platform_agent_still_builds_without_trace_experiment(self) -> None:
        agent = PlatformAgent(
            endpoint="model",
            workspace_client_factory=MagicMock(return_value=MagicMock()),
            settings_factory=MagicMock(),
            repository_factory=MagicMock(),
        )

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("backend.platform_agent.configure_mlflow_tracing") as configure,
            patch("langgraph.prebuilt.create_react_agent", return_value="graph") as create,
        ):
            self.assertEqual(agent.graph, "graph")

        configure.assert_not_called()
        create.assert_called_once()

    def test_platform_agent_returns_bounded_server_trace(self) -> None:
        graph = MagicMock()
        graph.invoke.return_value = {
            "messages": [AIMessage(content="Evidence-backed answer.")],
        }
        agent = PlatformAgent(
            endpoint="model",
            workspace_client_factory=MagicMock(return_value=MagicMock()),
            settings_factory=MagicMock(),
            repository_factory=MagicMock(),
        )
        agent.__dict__["graph"] = graph
        agent._mlflow_tracing_enabled = True
        span = MagicMock()
        span_context = MagicMock()
        span_context.__enter__.return_value = span

        with (
            patch(
                "backend.platform_agent.mlflow_span",
                return_value=span_context,
            ) as traced,
            patch(
                "backend.platform_agent.set_mlflow_span_outputs",
            ) as set_outputs,
        ):
            text, trace = agent.invoke_with_trace([{"role": "user", "content": "hello"}])

        self.assertEqual(text, "Evidence-backed answer.")
        self.assertEqual(trace["timing_source"], "server")
        self.assertIsNone(trace["ttft_ms"])
        self.assertIsNone(trace["tpot_ms"])
        self.assertEqual(trace["stages"][0]["category"], "llm_synthesis")
        traced.assert_called_once_with(
            "platform_agent.invoke",
            span_type="AGENT",
            inputs={
                "messages": [{"role": "user", "content": "hello"}],
                "endpoint": "model",
            },
            enabled=True,
        )
        set_outputs.assert_called_once_with(span, {"message": "Evidence-backed answer."})
        config = graph.invoke.call_args.kwargs["config"]
        self.assertEqual(config["recursion_limit"], 20)
        self.assertEqual(len(config["callbacks"]), 1)

    def test_trace_experiment_is_required(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "trace experiment"):
            configure_mlflow_tracing("")


if __name__ == "__main__":
    unittest.main()

"""MLflow tracing setup for the App-hosted LangGraph agent."""

from __future__ import annotations


def configure_mlflow_tracing(experiment_id: str) -> None:
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

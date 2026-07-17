"""Log, register and deploy the platform agent to Databricks model serving.

Run manually (or via workflow_dispatch) with workspace credentials and the
[agent] extra installed:

    pip install -e ".[agent]"
    python agents/platform_agent/deploy_agent.py

Registers the model to UC as <catalog>.<schema>.platform_agent and deploys a
serving endpoint via the Mosaic AI Agent Framework. NOT scheduled: agent
deployment is a deliberate human action.
"""

from __future__ import annotations

from pathlib import Path

import mlflow
from databricks import agents
from mlflow.models.resources import DatabricksServingEndpoint, DatabricksSQLWarehouse

from dbx_platform.config import Settings

HERE = Path(__file__).resolve().parent


def main() -> None:
    s = Settings.from_env()
    uc_model = f"{s.dashboard_catalog}.{s.dashboard_schema}.platform_agent"
    mlflow.set_registry_uri("databricks-uc")

    with mlflow.start_run(run_name="platform-agent"):
        logged = mlflow.pyfunc.log_model(
            name="platform_agent",
            python_model=str(HERE / "agent.py"),
            code_paths=[str(HERE / "tools.py"), str(HERE / "formatting.py")],
            pip_requirements=[
                "dbx-platform",
                "mlflow",
                "langgraph",
                "databricks-langchain",
            ],
            # Declared resources let serving mint scoped credentials for the
            # LLM endpoint and the SQL warehouse the tools query.
            resources=[
                DatabricksServingEndpoint(endpoint_name=s.digest_model),
                DatabricksSQLWarehouse(warehouse_id=s.warehouse_id),
            ],
            registered_model_name=uc_model,
        )

    deployment = agents.deploy(uc_model, _latest_version(uc_model),
                               environment_vars={
                                   "DBX_PLATFORM_WAREHOUSE_ID": s.warehouse_id,
                               })
    print(f"logged: {logged.model_uri}")
    print(f"deployed: {deployment.endpoint_name}")
    print("Chat via the AI Playground or the endpoint's review app.")


def _latest_version(uc_model: str) -> int:
    client = mlflow.MlflowClient(registry_uri="databricks-uc")
    versions = client.search_model_versions(f"name = '{uc_model}'")
    return max(int(v.version) for v in versions)


if __name__ == "__main__":
    main()

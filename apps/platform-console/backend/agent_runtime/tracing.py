"""MLflow tracing setup for the App-hosted LangGraph agent."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)


def configure_mlflow_tracing(experiment_id: str) -> bool:
    """Route native MLflow spans to the App-bound experiment."""

    if not experiment_id:
        raise RuntimeError("The App-bound MLflow trace experiment is not configured.")

    try:
        import mlflow
        from mlflow.entities.trace_location import MlflowExperimentLocation

        mlflow.set_tracking_uri("databricks")
        mlflow.tracing.set_destination(MlflowExperimentLocation(experiment_id))
    except Exception:
        log.warning(
            "MLflow trace destination is unavailable; continuing without tracing",
            exc_info=True,
        )
        return False
    return True


@contextmanager
def mlflow_span(
    name: str,
    *,
    span_type: str,
    inputs: dict[str, Any],
    enabled: bool,
) -> Iterator[Any | None]:
    """Create a native MLflow span without coupling tracing to availability."""

    if not enabled:
        yield None
        return

    try:
        import mlflow

        manager = mlflow.start_span(name=name, span_type=span_type)
        span = manager.__enter__()
    except Exception:
        log.warning("MLflow span could not start; continuing without tracing", exc_info=True)
        yield None
        return

    try:
        span.set_inputs(inputs)
    except Exception:
        log.warning("MLflow span inputs could not be recorded", exc_info=True)

    try:
        yield span
    except BaseException as app_error:
        try:
            manager.__exit__(
                type(app_error),
                app_error,
                app_error.__traceback__,
            )
        except Exception:
            log.warning("MLflow error span could not be finalized", exc_info=True)
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception:
            log.warning("MLflow span could not be finalized", exc_info=True)


def set_mlflow_span_outputs(span: Any | None, outputs: dict[str, Any]) -> None:
    """Record outputs when a live span exists, without affecting the request."""

    if span is None:
        return
    try:
        span.set_outputs(outputs)
    except Exception:
        log.warning("MLflow span outputs could not be recorded", exc_info=True)

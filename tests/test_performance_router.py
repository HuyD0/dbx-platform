import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "apps" / "platform-console"),
)

from backend import cache
from backend.routers import performance


def test_ai_gateway_telemetry_reads_only_persisted_gateway_rows(monkeypatch):
    cache.clear()
    monkeypatch.setattr(performance.deps, "control_plane_scope", lambda: ("w-1", "prod"))
    monkeypatch.setattr(
        performance.deps,
        "get_settings",
        lambda: SimpleNamespace(dashboard_catalog="main", dashboard_schema="platform"),
    )
    monkeypatch.setattr(performance.deps, "get_ws", lambda: object())
    monkeypatch.setattr(performance.deps, "warehouse_id", lambda: "wh-1")
    monkeypatch.setattr(
        performance.ai_monitor,
        "read_monitoring",
        lambda *_args: [
            {
                "usage_date": "2026-07-18",
                "endpoint_name": "gateway-endpoint",
                "app": "investment-analytics",
                "requests": 20,
                "input_tokens": 8_000,
                "output_tokens": 2_000,
                "p95_latency_ms": 480,
                "source": performance.ai_monitor.GATEWAY_USAGE_SOURCE,
                "unexpected_private_field": "not returned",
            },
            {
                "usage_date": "2026-07-18",
                "source": performance.ai_monitor.ENDPOINT_USAGE_SOURCE,
            },
        ],
    )

    response = performance.ai_gateway_telemetry(days=30, refresh=True)

    assert response["count"] == 1
    assert response["data"] == [
        {
            "usage_date": "2026-07-18",
            "endpoint_name": "gateway-endpoint",
            "app": "investment-analytics",
            "requests": 20,
            "input_tokens": 8_000,
            "output_tokens": 2_000,
            "p95_latency_ms": 480,
            "source": performance.ai_monitor.GATEWAY_USAGE_SOURCE,
        }
    ]
    assert response["source_status"]["status"] == "healthy"

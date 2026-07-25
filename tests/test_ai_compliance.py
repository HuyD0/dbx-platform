"""Pure compliance-posture aggregation tests; no workspace credentials."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

APP_DIR = Path(__file__).resolve().parent.parent / "apps" / "platform-console"
sys.path.insert(0, str(APP_DIR))

from backend import cache  # noqa: E402
from backend.routers import ai_governance  # noqa: E402
from backend.routers.ai_governance import build_compliance_posture  # noqa: E402


def test_posture_scores_only_attested_controls_and_flags_disabled_zdr():
    catalog = [
        {
            "source": "azure_openai",
            "model_key": "azure:/accounts/foundry-prod",
            "entity_type": "ACCOUNT",
            "endpoint_name": "foundry-prod",
            "resource_id": "/accounts/foundry-prod",
            "provider": "Microsoft Foundry",
            "key_auth_enabled": False,
            "details_json": json.dumps(
                {
                    "tags": {
                        "zdr_enabled": "false",
                        "content_safety_enabled": "true",
                        "audit_logging_enabled": "true",
                        "rate_limit_headroom_pct": "30",
                    }
                }
            ),
        },
        {
            "source": "databricks_serving",
            "model_key": "serving:assistant/prod.models.assistant",
            "entity_type": "CUSTOM_MODEL",
            "endpoint_name": "assistant",
            "resource_id": "assistant/prod.models.assistant",
            "provider": "databricks",
            "key_auth_enabled": False,
            "usage_tracking": True,
            "details_json": json.dumps(
                {
                    "governance": {
                        "zdr_enabled": True,
                        "content_safety_enabled": True,
                        "rate_limit_headroom_pct": 10,
                    }
                }
            ),
        },
    ]
    access = [
        {
            "source": "databricks_serving",
            "model_key": "serving:assistant",
            "principal_name": "users",
            "access_level": "CAN_QUERY",
        }
    ]

    posture = build_compliance_posture(catalog, access)
    by_id = {metric.id: metric for metric in posture.metrics}

    assert by_id["zdr"].value_pct == 50
    assert by_id["content_safety"].value_pct == 100
    assert by_id["access_control"].value_pct == 50
    assert by_id["audit_logging"].value_pct == 100
    assert by_id["rate_limit_headroom"].value_pct == 20
    assert posture.unverified_zdr_resources == 0
    assert len(posture.zdr_alerts) == 1
    assert posture.zdr_alerts[0].resource_name == "foundry-prod"


def test_missing_attestations_remain_unverified_instead_of_passing():
    posture = build_compliance_posture(
        [
            {
                "source": "azure_openai",
                "model_key": "azure:/accounts/unattested",
                "entity_type": "ACCOUNT",
                "endpoint_name": "unattested",
                "resource_id": "/accounts/unattested",
                "provider": "Microsoft Foundry",
                "key_auth_enabled": False,
                "details_json": "{}",
            }
        ],
        [],
    )

    zdr = next(metric for metric in posture.metrics if metric.id == "zdr")
    assert zdr.value_pct is None
    assert zdr.evaluated_resources == 0
    assert posture.unverified_zdr_resources == 1
    assert posture.zdr_alerts == []


def test_compliance_route_returns_a_scoped_typed_envelope(monkeypatch):
    cache.clear()
    monkeypatch.setattr(ai_governance.deps, "control_plane_scope", lambda: ("w-1", "prod"))
    monkeypatch.setattr(
        ai_governance.deps,
        "get_settings",
        lambda: SimpleNamespace(dashboard_catalog="main", dashboard_schema="platform"),
    )
    monkeypatch.setattr(ai_governance.deps, "get_ws", lambda: object())
    monkeypatch.setattr(ai_governance.deps, "warehouse_id", lambda: "warehouse-1")
    monkeypatch.setattr(
        ai_governance.ai_catalog,
        "read_catalog",
        lambda *_args, **_kwargs: [
            {
                "source": "azure_openai",
                "model_key": "azure:/accounts/secure",
                "entity_type": "ACCOUNT",
                "endpoint_name": "secure",
                "resource_id": "/accounts/secure",
                "provider": "Microsoft Foundry",
                "key_auth_enabled": False,
                "details_json": json.dumps({"tags": {"zdr_enabled": "true"}}),
            }
        ],
    )
    monkeypatch.setattr(
        ai_governance.ai_catalog,
        "read_access",
        lambda *_args, **_kwargs: [],
    )

    response = ai_governance.compliance(refresh=True)

    assert response["data"]["evaluated_resources"] == 1
    assert response["data"]["zdr_alerts"] == []
    assert response["data"]["metrics"][0]["value_pct"] == 100

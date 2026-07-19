"""Offline tests for the AI Cost Planner API and extraction module."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "apps" / "platform-console"
sys.path.insert(0, str(APP_DIR))

from backend import cache, deps  # noqa: E402

from dbx_platform.config import Settings  # noqa: E402

SNAPSHOT = "2026-07-14"

_PRICES = {
    "aoai.gpt-4o-mini.input": 0.15,
    "aoai.gpt-4o-mini.output": 0.60,
    "aoai.gpt-4o.input": 2.50,
    "aoai.gpt-4o.output": 10.00,
    "aoai.embedding-3-small.input": 0.02,
    "search.basic.unit": 0.11,
    "search.s1.unit": 0.34,
    "vm.d8s_v5": 0.384,
    "aks.standard_uptime": 0.10,
    "storage.hot_gb_month": 0.021,
    "postgres.flex.burstable": 0.017,
    "postgres.flex.general": 0.15,
    "dbx.model_serving.dbu": 0.07,
    "dbx.fmapi.dbu": 0.07,
    "dbx.vector_search.dbu": 0.07,
    "dbx.jobs_serverless.dbu": 0.35,
    "dbx.lakebase.dbu": 0.07,
}


def _snapshot_rows() -> list[dict]:
    return [
        {
            "snapshot_date": SNAPSHOT,
            "source": "azure_retail",
            "rate_key": key,
            "meter_name": f"meter:{key}",
            "unit_price": value,
            "currency": "USD",
        }
        for key, value in _PRICES.items()
    ]


@pytest.fixture()
def ws(monkeypatch) -> MagicMock:
    mock = MagicMock()
    monkeypatch.setattr(deps, "get_ws", lambda: mock)
    monkeypatch.setattr(deps, "get_settings", lambda: Settings(warehouse_id="wh-test"))
    cache.clear()
    # The local repository is a cached singleton; estimates saved in one test
    # must not leak into the next.
    deps.get_control_plane_repository.cache_clear()
    return mock


def _client(monkeypatch, roles: str):
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_IDENTITY", "true")
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_ACTOR_ID", "test-user")
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_ROLES", roles)
    deps.get_identity_verifier.cache_clear()
    from backend.app import create_app
    from fastapi.testclient import TestClient

    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture()
def client(ws, monkeypatch):
    with _client(monkeypatch, "operator") as test_client:
        yield test_client
    deps.get_identity_verifier.cache_clear()


@pytest.fixture()
def viewer_client(ws, monkeypatch):
    with _client(monkeypatch, "viewer") as test_client:
        yield test_client
    deps.get_identity_verifier.cache_clear()


# --- routes -------------------------------------------------------------------


def test_patterns_route_returns_plain_english_catalog(client):
    response = client.get("/api/estimator/patterns")
    assert response.status_code == 200
    data = response.json()["data"]
    assert {"doc_chat", "agent_workflow"} <= {p["pattern"] for p in data}
    assert all(p["label"] and p["description"] for p in data)


def test_estimate_rejects_invalid_requirements_with_plain_error(client):
    response = client.post(
        "/api/estimator/estimate",
        json={"requirements": {"pattern": "nope", "monthly_requests": 10}},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_requirements"
    assert "Unknown solution pattern" in response.json()["message"]


def test_estimate_degrades_when_no_snapshot_exists(client, monkeypatch):
    from dbx_platform import estimator_pricing

    monkeypatch.setattr(
        estimator_pricing, "read_latest_snapshot", lambda *a, **k: []
    )
    response = client.post(
        "/api/estimator/estimate",
        json={"requirements": {"pattern": "doc_chat", "monthly_requests": 1000}},
    )
    assert response.status_code == 503
    assert response.json()["error"] == "pricing_snapshot_missing"
    assert "estimator-prices-pull" in response.json()["hint"]


def test_estimate_returns_full_matrix_with_provenance(client, monkeypatch):
    from dbx_platform import estimator_pricing

    monkeypatch.setattr(
        estimator_pricing, "read_latest_snapshot", lambda *a, **k: _snapshot_rows()
    )
    response = client.post(
        "/api/estimator/estimate",
        json={
            "requirements": {"pattern": "doc_chat", "monthly_requests": 100000},
            "rigor_pct": 25,
        },
    )
    assert response.status_code == 200
    matrix = response.json()["data"]
    assert matrix["snapshot_date"] == SNAPSHOT
    assert matrix["rigor_pct"] == 25
    assert set(matrix["tiers"]) == {"prototype", "production", "fiduciary"}
    production = matrix["tiers"]["production"]["scenarios"]
    assert set(production) == {"databricks", "azure"}
    assert production["azure"]["totals_by_env"]["prod"] > 0
    assert production["azure"]["rigor_curve"]["by_env"]["prod"]["total_slope_per_pct"] > 0
    assert len(matrix["requirements_hash"]) == 64


def test_estimate_is_viewer_accessible(viewer_client, monkeypatch):
    from dbx_platform import estimator_pricing

    monkeypatch.setattr(
        estimator_pricing, "read_latest_snapshot", lambda *a, **k: _snapshot_rows()
    )
    response = viewer_client.post(
        "/api/estimator/estimate",
        json={"requirements": {"pattern": "summarize", "monthly_requests": 500}},
    )
    assert response.status_code == 200


def test_extract_requires_operator_role(viewer_client):
    response = viewer_client.post("/api/estimator/extract", json={"text": "chat bot"})
    assert response.status_code == 403


def test_extract_returns_requirements_and_warnings(client, monkeypatch):
    from backend import estimator_extraction
    from backend.routers import estimator as estimator_router

    monkeypatch.setattr(estimator_router, "_extraction_model", lambda: object())
    monkeypatch.setattr(
        estimator_extraction,
        "extract_requirements",
        lambda model, text: (
            {"pattern": "doc_chat", "monthly_requests": 4000},
            ["Converted '200 people a few times a day' to 4,000 requests/month."],
        ),
    )
    response = client.post(
        "/api/estimator/extract",
        json={"text": "200 people asking questions about our policy PDFs"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["requirements"]["pattern"] == "doc_chat"
    assert body["warnings"]


def test_pricing_status_degrades_without_table(client, monkeypatch):
    from dbx_platform import estimator_pricing

    def boom(*args, **kwargs):
        raise RuntimeError("table missing")

    monkeypatch.setattr(estimator_pricing, "read_snapshot_status", boom)
    response = client.get("/api/estimator/pricing-status")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["health"]["status"] == "unavailable"


# --- extraction module (pure, fake model) ------------------------------------


class FakeModel:
    """Queue of tool-call args returned per forced call."""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.calls.append({"tool": tools[0]["function"]["name"], "choice": tool_choice})
        return self

    def invoke(self, messages):
        self.calls[-1]["messages"] = messages
        return SimpleNamespace(tool_calls=[{"args": self.responses.pop(0)}])


def test_extraction_two_stage_flow_and_warning_propagation():
    from backend.estimator_extraction import extract_requirements

    model = FakeModel(
        [
            {"pattern": "doc_chat", "confident": True},
            {"monthly_requests": 4000, "corpus_gb": 2,
             "warnings": ["Assumed working-day usage."]},
        ]
    )
    requirements, warnings = extract_requirements(model, "policy chat for 200 people")
    assert requirements["pattern"] == "doc_chat"
    assert requirements["monthly_requests"] == 4000
    assert warnings == ["Assumed working-day usage."]
    assert [c["tool"] for c in model.calls] == ["pick_pattern", "record_requirements"]
    assert [c["choice"] for c in model.calls] == ["pick_pattern", "record_requirements"]
    # progressive disclosure: stage 1 sees only the compact catalog, user text last
    stage1 = model.calls[0]["messages"]
    assert stage1[0]["role"] == "system"
    assert "<user_description>" in stage1[-1]["content"]


def test_extraction_uncertain_pattern_adds_review_warning():
    from backend.estimator_extraction import extract_requirements

    model = FakeModel(
        [
            {"pattern": "summarize", "confident": False},
            {"monthly_requests": 100, "warnings": []},
        ]
    )
    _, warnings = extract_requirements(model, "shorten our reports")
    assert any("uncertain" in w for w in warnings)


def test_extraction_retries_once_then_fails_loud():
    from backend.estimator_extraction import ExtractionError, extract_requirements

    model = FakeModel(
        [
            {"pattern": "doc_chat", "confident": True},
            {"monthly_requests": 0, "warnings": []},  # invalid -> corrective retry
            {"monthly_requests": 2000, "warnings": []},
        ]
    )
    requirements, _ = extract_requirements(model, "docs chat")
    assert requirements["monthly_requests"] == 2000

    model = FakeModel(
        [
            {"pattern": "doc_chat", "confident": True},
            {"monthly_requests": 0, "warnings": []},
            {"monthly_requests": 0, "warnings": []},  # still invalid -> error
        ]
    )
    with pytest.raises(ExtractionError, match="requests per month"):
        extract_requirements(model, "docs chat")


def test_extraction_bounds_and_rejects_empty_text():
    from backend.estimator_extraction import ExtractionError, bound_text

    with pytest.raises(ExtractionError):
        bound_text("   ")
    assert len(bound_text("x" * 100_000)) == 8_000


def test_extraction_tool_schema_mirrors_requirements_fields():
    from dataclasses import fields

    from backend.estimator_extraction import build_extraction_tool

    from dbx_platform.estimator import Requirements, load_patterns

    tool = build_extraction_tool(load_patterns(), "doc_chat")
    properties = tool["function"]["parameters"]["properties"]
    for field in fields(Requirements):
        if field.name == "pattern":
            continue  # chosen in stage 1, not extracted
        assert field.name in properties, f"tool schema missing {field.name}"
    assert "warnings" in properties


# --- saved-estimate library ---------------------------------------------------


def _library_client(client, monkeypatch):
    from dbx_platform import estimator_pricing

    monkeypatch.setattr(
        estimator_pricing, "read_latest_snapshot", lambda *a, **k: _snapshot_rows()
    )
    return client


def test_save_estimate_recomputes_server_side_and_appears_in_library(
    client, monkeypatch
):
    _library_client(client, monkeypatch)
    response = client.post(
        "/api/estimator/estimates/record",
        json={
            "title": "Support doc chat",
            "requirements": {"pattern": "doc_chat", "monthly_requests": 4000},
            "rigor_pct": 25,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["requirements_hash"]) == 64
    assert body["snapshot_date"] == SNAPSHOT

    listing = client.get("/api/estimator/estimates").json()["data"]
    assert len(listing) == 1
    assert listing[0]["title"] == "Support doc chat"
    assert listing[0]["pattern"] == "doc_chat"
    assert "results_json" not in listing[0]


def test_save_estimate_requires_operator(viewer_client, monkeypatch):
    _library_client(viewer_client, monkeypatch)
    response = viewer_client.post(
        "/api/estimator/estimates/record",
        json={
            "title": "x",
            "requirements": {"pattern": "doc_chat", "monthly_requests": 10},
        },
    )
    assert response.status_code == 403


def test_save_estimate_fails_loud_without_snapshot(client, monkeypatch):
    from dbx_platform import estimator_pricing

    monkeypatch.setattr(estimator_pricing, "read_latest_snapshot", lambda *a, **k: [])
    response = client.post(
        "/api/estimator/estimates/record",
        json={
            "title": "x",
            "requirements": {"pattern": "doc_chat", "monthly_requests": 10},
        },
    )
    assert response.status_code == 503
    assert response.json()["error"] == "pricing_snapshot_missing"


def test_similar_estimates_exact_and_bracket_matching(client, monkeypatch):
    _library_client(client, monkeypatch)
    for requests_per_month, title in ((4000, "Sibling A"), (9000, "Sibling B"),
                                      (400_000, "Different scale")):
        assert (
            client.post(
                "/api/estimator/estimates/record",
                json={
                    "title": title,
                    "requirements": {
                        "pattern": "doc_chat",
                        "monthly_requests": requests_per_month,
                    },
                },
            ).status_code
            == 200
        )

    similar = client.get(
        "/api/estimator/estimates/similar",
        params={"pattern": "doc_chat", "monthly_requests": 5000},
    ).json()
    titles = {row["title"] for row in similar["similar"]}
    assert titles == {"Sibling A", "Sibling B"}  # same 10^3 bracket only
    assert similar["exact_match"] is None
    assert similar["bracket"] == {"lo": 1000, "hi": 10000}

    # exact match by requirements hash pulls the existing estimate up front
    saved_hash = client.post(
        "/api/estimator/estimates/record",
        json={
            "title": "Exact twin",
            "requirements": {"pattern": "doc_chat", "monthly_requests": 5000},
            "rigor_pct": 10,
        },
    ).json()["requirements_hash"]
    exact = client.get(
        "/api/estimator/estimates/similar",
        params={
            "pattern": "doc_chat",
            "monthly_requests": 5000,
            "requirements_hash": saved_hash,
        },
    ).json()
    assert exact["exact_match"]["title"] == "Exact twin"
    assert all(row["title"] != "Exact twin" for row in exact["similar"])

    # different pattern never matches
    other = client.get(
        "/api/estimator/estimates/similar",
        params={"pattern": "summarize", "monthly_requests": 5000},
    ).json()
    assert other["exact_match"] is None and other["similar"] == []


def test_viewer_sees_masked_created_by_in_library(client, monkeypatch):
    _library_client(client, monkeypatch)
    client.post(
        "/api/estimator/estimates/record",
        json={
            "title": "Masked",
            "requirements": {"pattern": "summarize", "monthly_requests": 100},
        },
    )
    as_operator = client.get("/api/estimator/estimates").json()["data"]
    assert as_operator and as_operator[0]["created_by"] == "test-user"

    # Same app, downgraded identity: the redaction boundary must catch the key.
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_ROLES", "viewer")
    deps.get_identity_verifier.cache_clear()
    as_viewer = client.get("/api/estimator/estimates").json()["data"]
    assert as_viewer and as_viewer[0]["created_by"] == "[redacted]"


# --- document upload ----------------------------------------------------------


def test_extract_document_parses_and_extracts(client, monkeypatch):
    from backend import estimator_extraction
    from backend.routers import estimator as estimator_router

    monkeypatch.setattr(estimator_router, "_extraction_model", lambda: object())
    monkeypatch.setattr(
        estimator_extraction,
        "extract_requirements",
        lambda model, text: (
            {"pattern": "doc_chat", "monthly_requests": 1000},
            [f"Read {len(text)} characters."],
        ),
    )
    response = client.post(
        "/api/estimator/extract-document",
        files={"file": ("brief.md", b"# Chat over policy docs, 1000/mo", "text/markdown")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["requirements"]["pattern"] == "doc_chat"
    assert body["filename"] == "brief.md"
    assert body["source"] == "document"


def test_extract_document_routes_images_through_the_vision_path(client, monkeypatch):
    from backend import estimator_extraction
    from backend.routers import estimator as estimator_router

    monkeypatch.setattr(estimator_router, "_extraction_model", lambda: object())
    captured = {}

    def _from_image(model, filename, data):
        captured["filename"] = filename
        captured["bytes"] = len(data)
        return {"pattern": "doc_chat", "monthly_requests": 4000}, ["Read from a diagram."]

    monkeypatch.setattr(estimator_extraction, "extract_from_image", _from_image)
    response = client.post(
        "/api/estimator/extract-document",
        files={"file": ("architecture.png", b"\x89PNG fake bytes", "image/png")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "diagram"
    assert body["requirements"]["pattern"] == "doc_chat"
    assert captured["filename"] == "architecture.png"


def test_extract_document_rejects_unsupported_types_plainly(client, monkeypatch):
    from backend.routers import estimator as estimator_router

    monkeypatch.setattr(estimator_router, "_extraction_model", lambda: object())
    response = client.post(
        "/api/estimator/extract-document",
        files={"file": ("data.xlsx", b"PK\x03\x04", "application/octet-stream")},
    )
    assert response.status_code == 422
    assert "file type is not supported" in response.json()["message"]


def test_extract_document_enforces_the_streamed_size_cap(client):
    oversized = b"x" * (10 * 1024 * 1024 + 1)
    response = client.post(
        "/api/estimator/extract-document",
        files={"file": ("big.txt", oversized, "text/plain")},
    )
    assert response.status_code == 413
    assert response.json()["error"] == "document_too_large"


def test_extract_document_requires_operator(viewer_client):
    response = viewer_client.post(
        "/api/estimator/extract-document",
        files={"file": ("brief.md", b"hello", "text/markdown")},
    )
    assert response.status_code == 403


# --- deploy-link (estimate → real cost anchor) --------------------------------


def _save_estimate(client) -> str:
    return client.post(
        "/api/estimator/estimates/record",
        json={"title": "Doc chat", "rigor_pct": 10,
              "requirements": {"pattern": "doc_chat", "monthly_requests": 100000}},
    ).json()["estimate_id"]


def test_link_deployment_reads_projection_server_side_and_lists(client, monkeypatch):
    _library_client(client, monkeypatch)
    estimate_id = _save_estimate(client)
    response = client.post(
        "/api/estimator/deployments/link",
        json={"estimate_id": estimate_id, "tier": "production", "scenario": "azure",
              "anchor_kind": "azure_resource_group", "anchor_value": "rg-doc-chat"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["deployment_id"]
    assert body["monthly_projected_usd"] > 0  # read from the estimate, not the client

    rows = client.get("/api/estimator/deployments").json()["data"]
    assert len(rows) == 1
    assert rows[0]["anchor_value"] == "rg-doc-chat"
    assert rows[0]["tier"] == "production"


def test_link_deployment_rejects_bad_tier_scenario_anchor(client, monkeypatch):
    _library_client(client, monkeypatch)
    estimate_id = _save_estimate(client)
    for bad in (
        {"tier": "nope", "scenario": "azure", "anchor_kind": "azure_resource_group"},
        {"tier": "production", "scenario": "nope", "anchor_kind": "azure_resource_group"},
        {"tier": "production", "scenario": "azure", "anchor_kind": "made_up_anchor"},
    ):
        response = client.post(
            "/api/estimator/deployments/link",
            json={"estimate_id": estimate_id, "anchor_value": "x", **bad},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_deployment"


def test_link_deployment_rejects_unknown_estimate(client, monkeypatch):
    _library_client(client, monkeypatch)
    response = client.post(
        "/api/estimator/deployments/link",
        json={"estimate_id": "does-not-exist", "tier": "production", "scenario": "azure",
              "anchor_kind": "azure_resource_group", "anchor_value": "rg"},
    )
    assert response.status_code == 422


def test_link_deployment_requires_operator(viewer_client):
    response = viewer_client.post(
        "/api/estimator/deployments/link",
        json={"estimate_id": "x", "tier": "production", "scenario": "azure",
              "anchor_kind": "azure_resource_group", "anchor_value": "rg"},
    )
    assert response.status_code == 403

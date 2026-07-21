"""Pure-logic tests for the AI Solution Cost & TCO engine (no network, no SDK)."""

from __future__ import annotations

import json
import re

import pytest

from dbx_platform import estimator
from dbx_platform.estimator import (
    ENVS,
    SCENARIOS,
    TIERS,
    Requirements,
    build_price_book,
    compute_estimate,
    compute_matrix,
    load_patterns,
    load_rate_card,
    load_tiers,
    requirements_hash,
    scale_bracket,
    validate_requirements,
)

SNAPSHOT_DATE = "2026-07-14"

_FIXTURE_PRICES = {
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


def price_rows(overrides: dict | None = None, drop: set[str] | None = None) -> list[dict]:
    prices = {**_FIXTURE_PRICES, **(overrides or {})}
    return [
        {
            "snapshot_date": SNAPSHOT_DATE,
            "source": "azure_retail" if not key.startswith("dbx.") else "databricks_list_prices",
            "rate_key": key,
            "meter_name": f"meter:{key}",
            "unit_price": value,
            "currency": "USD",
        }
        for key, value in prices.items()
        if key not in (drop or set())
    ]


def book(overrides: dict | None = None, drop: set[str] | None = None):
    return build_price_book(price_rows(overrides, drop), load_rate_card())


DOC_CHAT = Requirements(pattern="doc_chat", monthly_requests=100_000, corpus_gb=10.0)


# --- validation ---------------------------------------------------------------


def test_validate_requirements_happy_path():
    req = validate_requirements({"pattern": "doc_chat", "monthly_requests": 5000})
    assert req.pattern == "doc_chat"
    assert req.monthly_requests == 5000
    assert req.currency == "USD"


def test_validate_requirements_rejects_unknown_pattern():
    with pytest.raises(ValueError, match="Unknown solution pattern"):
        validate_requirements({"pattern": "quantum", "monthly_requests": 10})


def test_validate_requirements_bounds_are_plain_english():
    with pytest.raises(ValueError, match="requests per month"):
        validate_requirements({"pattern": "doc_chat", "monthly_requests": 0})
    with pytest.raises(ValueError, match="steps per task"):
        validate_requirements(
            {"pattern": "doc_chat", "monthly_requests": 10, "agent_steps": 999}
        )


# --- hashing & similarity -----------------------------------------------------


def test_requirements_hash_is_deterministic_and_version_sensitive():
    h1 = requirements_hash(
        DOC_CHAT, rigor_pct=10, rate_card_version="v1", snapshot_date=SNAPSHOT_DATE
    )
    h2 = requirements_hash(
        DOC_CHAT, rigor_pct=10, rate_card_version="v1", snapshot_date=SNAPSHOT_DATE
    )
    assert h1 == h2
    assert h1 != requirements_hash(
        DOC_CHAT, rigor_pct=11, rate_card_version="v1", snapshot_date=SNAPSHOT_DATE
    )
    assert h1 != requirements_hash(
        DOC_CHAT, rigor_pct=10, rate_card_version="v2", snapshot_date=SNAPSHOT_DATE
    )


def test_scale_bracket_buckets_by_order_of_magnitude():
    assert scale_bracket(9) == 0
    assert scale_bracket(100_000) == 5
    assert scale_bracket(999_999) == 5
    assert scale_bracket(0) == 0


# --- hand-derived golden line item -------------------------------------------


def test_doc_chat_prod_model_reading_cost_matches_hand_math():
    """100k req × 1 step × (300 + 6×400) text units = 270M.

    Prototype tier uses the efficient model: 270M × $0.15/1M = $40.50.
    Production tier uses the standard model: 270M × $2.50/1M = $675.00.
    """

    b = book()
    for tier, expected in (("prototype", 40.50), ("production", 675.00)):
        est = compute_estimate(DOC_CHAT, tier=tier, rigor_pct=10, scenario="azure", price_book=b)
        [item] = [
            i
            for i in est["line_items"]
            if i["component"] == "model_tokens" and i["env"] == "prod" and "reading" in i["label"]
        ]
        assert item["quantity"] == 270.0
        assert item["monthly_cost"] == expected
        assert "270.00 million text units" in item["formula"]


def test_databricks_token_price_uses_rate_card_dbu_constant_with_provenance():
    est = compute_estimate(
        DOC_CHAT, tier="production", rigor_pct=10, scenario="databricks", price_book=book()
    )
    [item] = [
        i
        for i in est["line_items"]
        if i["component"] == "model_tokens" and i["env"] == "prod" and "reading" in i["label"]
    ]
    # answer_standard: 21.4 DBU per 1M × $0.07/DBU = $1.498 per 1M × 270M
    assert item["monthly_cost"] == round(270.0 * 21.4 * 0.07, 2)
    assert "captured 2026-06" in item["provenance"]


# --- structural invariants ----------------------------------------------------


def _all_estimates():
    b = book()
    for tier in TIERS:
        for scenario in SCENARIOS:
            yield compute_estimate(
                DOC_CHAT, tier=tier, rigor_pct=10, scenario=scenario, price_book=b
            )


def test_totals_equal_sum_of_line_items_and_split_into_run_plus_eval():
    for est in _all_estimates():
        for env in ENVS:
            items = [
                i["monthly_cost"]
                for i in est["line_items"]
                if i["env"] == env and i["monthly_cost"] is not None
            ]
            assert est["totals_by_env"][env] == pytest.approx(sum(items), abs=0.02)
            assert est["totals_by_env"][env] == pytest.approx(
                est["run_cost_by_env"][env] + est["eval_tax_by_env"][env], abs=0.02
            )


def test_every_priced_line_item_is_quantity_times_unit_price():
    for est in _all_estimates():
        for item in est["line_items"]:
            if item["monthly_cost"] is None:
                continue
            assert item["monthly_cost"] == pytest.approx(
                item["quantity"] * item["unit_price"], abs=0.01
            )


def test_prototype_has_zero_ai_graded_review_spend():
    for scenario in SCENARIOS:
        est = compute_estimate(
            DOC_CHAT, tier="prototype", rigor_pct=50, scenario=scenario, price_book=book()
        )
        judge_cost = sum(
            i["monthly_cost"] or 0
            for i in est["line_items"]
            if i["component"] == "eval_judge_tokens"
        )
        assert judge_cost == 0
        assert est["rigor_pct"] == 0  # locked


def test_eval_tax_orders_fiduciary_above_production_above_prototype():
    b = book()
    for scenario in SCENARIOS:
        by_tier = {
            tier: sum(
                compute_estimate(
                    DOC_CHAT, tier=tier, rigor_pct=10, scenario=scenario, price_book=b
                )["eval_tax_by_env"].values()
            )
            for tier in TIERS
        }
        assert by_tier["fiduciary"] > by_tier["production"] > by_tier["prototype"]


def test_improvement_pipeline_is_a_subset_of_eval_tax_and_dev_uat_only():
    for est in _all_estimates():
        for env in ENVS:
            assert (
                est["improvement_pipeline_by_env"][env] <= est["eval_tax_by_env"][env] + 0.01
            )
        for item in est["line_items"]:
            if item["eval_group"] == "improvement_pipeline":
                assert item["env"] in ("dev", "uat")
                assert item["is_eval_tax"]


def test_state_store_follows_needs_memory():
    b = book()
    components = lambda est: {i["component"] for i in est["line_items"]}  # noqa: E731
    no_memory = compute_estimate(
        DOC_CHAT, tier="production", rigor_pct=10, scenario="databricks", price_book=b
    )
    assert "state_store" not in components(no_memory)
    agent = compute_estimate(
        Requirements(pattern="agent_workflow", monthly_requests=10_000),
        tier="production", rigor_pct=10, scenario="databricks", price_book=b,
    )
    assert "state_store" in components(agent)
    overridden = compute_estimate(
        Requirements(pattern="doc_chat", monthly_requests=10_000, needs_memory=True),
        tier="production", rigor_pct=10, scenario="azure", price_book=b,
    )
    assert "state_store" in components(overridden)


# --- rigor curve --------------------------------------------------------------


def test_rigor_curve_is_exactly_affine():
    matrix = compute_matrix(DOC_CHAT, rigor_pct=37, price_book=book())
    est = matrix["tiers"]["production"]["scenarios"]["azure"]
    curve = est["rigor_curve"]["by_env"]
    for env in ENVS:
        predicted = curve[env]["total_fixed"] + curve[env]["total_slope_per_pct"] * 37
        assert est["totals_by_env"][env] == pytest.approx(predicted, abs=0.51)
        predicted_eval = curve[env]["eval_fixed"] + curve[env]["eval_slope_per_pct"] * 37
        assert est["eval_tax_by_env"][env] == pytest.approx(predicted_eval, abs=0.51)


def test_rigor_locked_tiers_are_pinned():
    matrix = compute_matrix(DOC_CHAT, rigor_pct=42, price_book=book())
    assert matrix["tiers"]["prototype"]["scenarios"]["azure"]["rigor_curve"]["pinned"]
    assert matrix["tiers"]["fiduciary"]["scenarios"]["azure"]["rigor_curve"]["pinned"]
    assert not matrix["tiers"]["production"]["scenarios"]["azure"]["rigor_curve"]["pinned"]
    assert matrix["tiers"]["fiduciary"]["scenarios"]["azure"]["rigor_pct"] == 100


# --- missing prices are loud --------------------------------------------------


def test_missing_price_yields_none_cost_and_explicit_flag_never_zero():
    est = compute_estimate(
        DOC_CHAT, tier="production", rigor_pct=10, scenario="azure",
        price_book=book(drop={"storage.hot_gb_month"}),
    )
    storage_items = [i for i in est["line_items"] if i["component"] == "trace_storage"]
    assert storage_items
    assert all(i["monthly_cost"] is None for i in storage_items)
    assert all("price unavailable" in i["formula"] for i in storage_items)
    assert any("trace_storage" in m for m in est["missing_prices"])


# --- matrix shape -------------------------------------------------------------


def test_matrix_covers_all_tiers_scenarios_with_provenance():
    matrix = compute_matrix(DOC_CHAT, rigor_pct=10, price_book=book())
    assert matrix["engine_version"] == estimator.ENGINE_VERSION
    assert matrix["snapshot_date"] == SNAPSHOT_DATE
    assert matrix["rate_card_version"] == load_rate_card()["version"]
    assert len(matrix["requirements_hash"]) == 64
    assert set(matrix["tiers"]) == set(TIERS)
    for tier in TIERS:
        assert set(matrix["tiers"][tier]["scenarios"]) == set(SCENARIOS)
    titles = [s["title"] for s in matrix["blueprint"]]
    assert "How it stays reviewable" in titles
    assert "Where it can run" in titles


# --- zero-jargon rule ---------------------------------------------------------

_BANNED = re.compile(
    r"(?i)\b(rag|vector|llm|inference|token|embedding|checkpoint|langgraph|pipeline)\b"
)


def _user_facing_strings() -> list[str]:
    patterns = load_patterns()
    tiers = load_tiers()
    texts: list[str] = []
    for p in patterns["patterns"].values():
        texts += [p["label"], p["description"], p["example_prompt"]]
        texts += [s["title"] for s in p["blueprint"]] + [s["body"] for s in p["blueprint"]]
    for s in patterns.get("shared_blueprint", []):
        texts += [s["title"], s["body"]]
    for t in tiers["tiers"].values():
        texts += [t["label"], t["description"], t["rigor_locked_reason"]]
    est = compute_estimate(
        Requirements(pattern="agent_workflow", monthly_requests=1000),
        tier="fiduciary", rigor_pct=100, scenario="azure", price_book=book(),
    )
    texts += [i["label"] for i in est["line_items"]]
    return texts


def test_user_facing_strings_contain_no_jargon():
    offenders = [t for t in _user_facing_strings() if _BANNED.search(t)]
    assert not offenders, f"jargon found in user-facing strings: {offenders}"


# --- packaged data sanity -----------------------------------------------------


def test_rate_card_models_reference_defined_rate_keys():
    card = load_rate_card()
    azure_keys = {r["rate_key"] for r in card["azure_rate_keys"]}
    dbx_keys = {r["rate_key"] for r in card["databricks_rate_keys"]}
    for name, model in card["models"].items():
        for direction, key in model.get("azure", {}).items():
            assert key in azure_keys, f"{name}.azure.{direction} -> {key} undefined"
        assert model["databricks"]["dbu_rate_key"] in dbx_keys


def test_rate_card_regexes_compile():
    card = load_rate_card()
    for entry in card["azure_rate_keys"]:
        re.compile(entry["meter_regex"])
        for optional in ("exclude_regex", "product_regex", "product_exclude_regex"):
            if entry.get(optional):
                re.compile(entry[optional])
        assert entry["group"] in card["azure_groups"]
    for entry in card["databricks_rate_keys"]:
        re.compile(entry["sku_regex"])


def test_estimate_is_json_serializable():
    matrix = compute_matrix(DOC_CHAT, rigor_pct=10, price_book=book())
    json.dumps(matrix)


def test_similar_bracket_bounds_cover_the_same_order_of_magnitude():
    from dbx_platform.estimator import similar_bracket_bounds

    assert similar_bracket_bounds(4000) == (1000, 10_000)
    assert similar_bracket_bounds(100_000) == (100_000, 1_000_000)
    assert similar_bracket_bounds(5) == (1, 10)
    lo, hi = similar_bracket_bounds(999)
    assert lo <= 999 < hi


def test_estimates_table_ddl_is_append_only_with_filter_columns():
    from dbx_platform.estimator import create_estimates_table_sql

    ddl = create_estimates_table_sql("cat", "sch")
    assert "cat.sch.estimator_estimates" in ddl
    assert "'delta.appendOnly' = 'true'" in ddl
    for column in ("pattern STRING", "monthly_requests BIGINT", "requirements_hash"):
        assert column in ddl

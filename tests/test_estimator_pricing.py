"""Pure-logic tests for estimator price snapshot ingestion (no network)."""

from __future__ import annotations

import pytest

from dbx_platform.estimator import load_rate_card
from dbx_platform.estimator_pricing import (
    PRICE_ROW_SCHEMA,
    _capacity_scale,
    _token_scale,
    build_price_filters,
    classify_price_coverage,
    create_price_snapshot_table_sql,
    databricks_prices_sql,
    latest_snapshot_sql,
    merge_price_snapshot_sql,
    parse_databricks_prices,
    parse_retail_prices,
    store_price_snapshot,
)

SNAPSHOT = "2026-07-14"


def _item(meter, price, *, unit="1K", product="gpt 4o mini", region="eastus", **extra):
    return {
        "meterName": meter,
        "retailPrice": price,
        "unitOfMeasure": unit,
        "productName": product,
        "skuName": "S0",
        "serviceName": "Cognitive Services",
        "armRegionName": region,
        "meterId": extra.get("meterId", f"id-{meter}-{region}"),
        "currencyCode": "USD",
    }


# --- filters ------------------------------------------------------------------


def test_build_price_filters_covers_referenced_groups_with_region_and_global():
    filters = dict(build_price_filters(load_rate_card(), "eastus", "USD"))
    assert "aoai" in filters and "search" in filters and "storage" in filters
    for clause in filters.values():
        assert "armRegionName eq 'eastus'" in clause
        assert "armRegionName eq 'Global'" in clause


# --- unit normalization -------------------------------------------------------


def test_token_scale_normalizes_to_per_million():
    assert _token_scale("1K") == 1000
    assert _token_scale("10K") == 100
    assert _token_scale("1M") == 1
    assert _token_scale("1,000") == 1000
    assert _token_scale("") is None
    assert _token_scale("Each") is None


def test_capacity_scale_normalizes_per_n_units():
    assert _capacity_scale("1 Hour") == 1.0
    assert _capacity_scale("100 GB/Month") == 0.01
    assert _capacity_scale("1 GB/Month") == 1.0


# --- retail parse -------------------------------------------------------------


def test_parse_retail_prices_matches_normalizes_and_excludes():
    items = {
        "aoai": [
            _item("gpt 4o mini Input Tokens", 0.00015, unit="1K"),
            _item("gpt 4o mini Cached Input Tokens", 0.00007, unit="1K"),  # excluded
            _item("gpt 4o mini Output Tokens", 0.0006, unit="1K"),
            _item("gpt 4o Input Tokens", 0.0025, unit="1K", product="gpt 4o"),
            _item("gpt 4o mini Input Tokens", 0.0, unit="1K", meterId="zero"),  # skipped
        ]
    }
    rows = parse_retail_prices(items, load_rate_card(), SNAPSHOT)
    by_key = {}
    for row in rows:
        by_key.setdefault(row["rate_key"], []).append(row)
    mini_in = by_key["aoai.gpt-4o-mini.input"]
    assert len(mini_in) == 1  # cached excluded, zero-price skipped
    assert mini_in[0]["unit_price"] == pytest.approx(0.15)  # per 1M
    assert mini_in[0]["unit"] == "million text units"
    assert mini_in[0]["snapshot_date"] == SNAPSHOT
    # negative lookahead: plain gpt-4o meters never land under the mini key
    assert all("mini" in r["meter_name"].lower() for r in mini_in)
    assert by_key["aoai.gpt-4o.input"][0]["unit_price"] == pytest.approx(2.5)


def test_parse_retail_prices_vm_excludes_spot_and_windows():
    items = {
        "vm": [
            _item("D8s v5", 0.384, unit="1 Hour", product="Virtual Machines Dsv5 Series"),
            _item("D8s v5 Spot", 0.05, unit="1 Hour", product="Virtual Machines Dsv5 Series"),
            _item(
                "D8s v5", 0.75, unit="1 Hour",
                product="Virtual Machines Dsv5 Series Windows", meterId="win",
            ),
        ]
    }
    rows = [r for r in parse_retail_prices(items, load_rate_card(), SNAPSHOT)
            if r["rate_key"] == "vm.d8s_v5"]
    assert len(rows) == 1
    assert rows[0]["unit_price"] == pytest.approx(0.384)


# --- databricks parse ---------------------------------------------------------


def test_parse_databricks_prices_matches_skus():
    rows = parse_databricks_prices(
        [
            {"sku_name": "PREMIUM_SERVERLESS_REAL_TIME_INFERENCE_EASTUS",
             "currency_code": "USD", "unit_price": "0.07"},
            {"sku_name": "PREMIUM_JOBS_SERVERLESS_COMPUTE_EASTUS",
             "currency_code": "USD", "unit_price": 0.35},
            {"sku_name": "PREMIUM_ALL_PURPOSE_COMPUTE", "currency_code": "USD",
             "unit_price": 0.55},  # matches no rate key
            {"sku_name": "PREMIUM_VECTOR_SEARCH_EASTUS", "currency_code": "USD",
             "unit_price": None},  # unparseable price skipped
        ],
        load_rate_card(),
        SNAPSHOT,
    )
    keys = {r["rate_key"] for r in rows}
    assert keys == {"dbx.model_serving.dbu", "dbx.jobs_serverless.dbu"}
    assert all(r["source"] == "databricks_list_prices" for r in rows)
    assert all(r["unit"] == "DBU" for r in rows)


def test_databricks_prices_sql_reads_current_azure_list_prices():
    sql = databricks_prices_sql()
    assert "system.billing.list_prices" in sql
    assert "price_end_time IS NULL" in sql
    assert "AZURE" in sql


# --- DDL / MERGE / read -------------------------------------------------------


def test_ddl_and_merge_sql_shape():
    ddl = create_price_snapshot_table_sql("cat", "sch")
    assert "cat.sch.estimator_price_snapshots" in ddl
    assert "IF NOT EXISTS" in ddl
    merge = merge_price_snapshot_sql("cat", "sch")
    assert PRICE_ROW_SCHEMA in merge
    assert "WHEN NOT MATCHED BY SOURCE" in merge
    assert ":snapshot_date" in merge and ":environment" in merge
    latest = latest_snapshot_sql("cat", "sch")
    assert "MAX(snapshot_date)" in latest


def test_store_price_snapshot_rejects_rows_outside_snapshot():
    rows = [{"snapshot_date": "2026-01-01", "rate_key": "x"}]
    with pytest.raises(ValueError, match="outside the reconciliation window|outside snapshot"):
        store_price_snapshot(
            object(), "wh", "cat", "sch", rows,
            snapshot_date=SNAPSHOT, environment="prod",
        )
    with pytest.raises(ValueError, match="environment"):
        store_price_snapshot(
            object(), "wh", "cat", "sch", [], snapshot_date=SNAPSHOT, environment=" ",
        )


# --- coverage -----------------------------------------------------------------


def test_classify_price_coverage_flags_required_gaps_and_notes_optional():
    card = load_rate_card()
    required = [e["rate_key"] for e in card["azure_rate_keys"] if not e.get("optional")]
    rows = [
        {"snapshot_date": SNAPSHOT, "rate_key": key}
        for key in required + [e["rate_key"] for e in card["databricks_rate_keys"]]
        if key != "search.s1.unit"
    ]
    findings, notes = classify_price_coverage(card, rows, SNAPSHOT)
    assert [f["rate_key"] for f in findings] == ["search.s1.unit"]
    assert findings[0]["action"] == "update-rate-card-meter-regex"
    # optional foundry keys were absent from rows -> notes, not findings
    assert any("foundry" in n for n in notes)


def test_classify_price_coverage_full_snapshot_is_clean():
    card = load_rate_card()
    all_keys = [e["rate_key"] for e in card["azure_rate_keys"]] + [
        e["rate_key"] for e in card["databricks_rate_keys"]
    ]
    findings, _ = classify_price_coverage(
        card, [{"snapshot_date": SNAPSHOT, "rate_key": k} for k in all_keys], SNAPSHOT
    )
    assert findings == []


# --- CLI wiring ---------------------------------------------------------------


def test_cli_estimator_parsers_are_wired():
    from dbx_platform import cli

    parser = cli.build_parser()
    args = parser.parse_args(["estimator", "prices-pull", "--region", "westus2"])
    assert args.func is cli.cmd_estimator_prices_pull
    assert args.region == "westus2"
    assert args.approved_action_id == ""  # governed_write parent present
    args = parser.parse_args(["estimator", "prices-status"])
    assert args.func is cli.cmd_estimator_prices_status
    args = parser.parse_args(["estimator", "patterns"])
    assert args.func is cli.cmd_estimator_patterns
    args = parser.parse_args(
        ["estimator", "estimate", "--requirements-file", "req.json", "--rigor", "25"]
    )
    assert args.func is cli.cmd_estimator_estimate
    assert args.rigor == 25
    with pytest.raises(SystemExit):
        parser.parse_args(["estimator", "estimate"])  # requirements file is mandatory


def test_cli_prices_pull_refuses_without_governed_context(monkeypatch):
    from unittest.mock import MagicMock

    from dbx_platform import approved_job, cli

    monkeypatch.setattr(cli, "get_client", lambda profile: MagicMock())

    def _reject(*args, **kwargs):
        raise approved_job.ApprovalGateError("no verified executor context")

    monkeypatch.setattr(approved_job, "verify_governed_write_launch", _reject)
    parser = cli.build_parser()
    args = parser.parse_args(["estimator", "prices-pull"])
    assert args.func(args) == 2

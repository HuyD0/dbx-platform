from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from dbx_platform import application_cost
from dbx_platform.application_cost import (
    append_application_evidence,
    application_table_grant_statements,
    azure_evidence_snapshot_id,
    build_portfolio,
    build_profile,
    create_application_table_statements,
    decode_cursor,
    encode_cursor,
    fetch_application_bindings,
    normalize_application_key,
    paginate,
    parse_application_tag_keys,
    prepare_azure_evidence_snapshot,
    prepare_azure_resource_evidence,
    prepare_binding_snapshot,
    prepare_source_health,
    prepare_tag_evidence,
    read_application_evidence,
    read_azure_resource_evidence_sql,
    read_azure_rows_sql,
    read_azure_tag_rows_sql,
    resolve_application_identity,
    resolve_evidence_rows,
)


def _row(**overrides):
    row = {
        "workspace_id": "w1",
        "environment": "prod",
        "usage_date": "2026-07-25",
        "source": "databricks",
        "resource_type": "job",
        "resource_id": "j1",
        "resource_name": "training",
        "service": "JOBS_SERVERLESS",
        "workload": "JOBS",
        "cost": 4,
        "currency": "USD",
        "pricing_basis": "DATABRICKS_LIST",
        "evidence_at": "2026-07-25T12:00:00Z",
        "scope": "workspace:w1",
    }
    row.update(overrides)
    return row


def test_identity_keys_are_generic_normalized_and_configurable():
    assert parse_application_tag_keys(None) == ("application", "app", "project")
    assert parse_application_tag_keys(" APP,project,app ") == ("app", "project")
    assert normalize_application_key("  Learn APP ") == "learn-app"


def test_metadata_tag_and_binding_that_agree_are_exact():
    resolved = resolve_application_identity(
        metadata_application="Learn App",
        bound_application="learn app",
        tags={"project": "LEARN APP"},
    )
    assert resolved == {
        "application_key": "learn-app",
        "raw_application": "Learn App",
        "attribution_method": "DIRECT_METADATA",
        "tag_key": None,
        "conflict_values": [],
    }


def test_conflicting_accepted_tags_fail_closed():
    resolved = resolve_application_identity(
        tags={"application": "learn-app", "project": "shared-platform"},
    )
    assert resolved["attribution_method"] == "CONFLICT"
    assert resolved["application_key"] is None
    assert resolved["conflict_values"] == ["learn-app", "shared-platform"]


def test_nonidentity_tags_remain_audit_evidence_not_alignment_conflicts():
    evidence = resolve_evidence_rows(
        [
            _row(
                tags={"application": "learn-app", "team": "ml", "env": "prod"}
            )
        ]
    )
    assert evidence[0]["tags"] == {
        "application": "learn-app",
        "team": "ml",
        "env": "prod",
    }
    assert evidence[0]["identity_tags"] == {"application": "learn-app"}
    profile = build_profile("learn-app", evidence)
    assert profile is not None
    assert {
        (row["tag_key"], row["status"]) for row in profile["tag_alignment"]
    } == {("application", "matched")}


def test_untagged_shared_azure_cost_is_not_claimed():
    resolved = resolve_application_identity(tags={}, shared_scope=True)
    assert resolved["attribution_method"] == "SHARED_UNALLOCATED"
    assert resolved["application_key"] is None


def test_binding_is_effective_only_from_first_observation():
    bindings = [
        {
            "resource_type": "job",
            "resource_id": "j1",
            "raw_application": "learn-app",
            "effective_from": "2026-07-20T00:00:00Z",
        }
    ]
    rows = resolve_evidence_rows(
        [
            _row(usage_date="2026-07-19"),
            _row(usage_date="2026-07-20"),
            _row(usage_date="2026-07-21"),
        ],
        bindings=bindings,
    )
    assert rows[0]["attribution_method"] == "UNATTRIBUTED"
    assert rows[1]["attribution_method"] == "UNATTRIBUTED"
    assert rows[2]["attribution_method"] == "DIRECT_RESOURCE"


def test_removed_binding_expires_at_next_complete_snapshot():
    bindings = [
        {
            "resource_type": "job",
            "resource_id": "j1",
            "raw_application": "learn-app",
            "effective_from": "2026-07-20T00:00:00Z",
            "effective_to": "2026-07-22T00:00:00Z",
        }
    ]
    rows = resolve_evidence_rows(
        [
            _row(usage_date="2026-07-21"),
            _row(usage_date="2026-07-22"),
        ],
        bindings=bindings,
    )
    assert rows[0]["attribution_method"] == "DIRECT_RESOURCE"
    assert rows[1]["attribution_method"] == "UNATTRIBUTED"


def test_portfolio_and_profile_never_blend_bases_or_currencies():
    evidence = resolve_evidence_rows(
        [
            _row(metadata_application="learn-app", cost=5),
            _row(
                source="azure",
                resource_type="azure_resource",
                resource_id="/subscriptions/s/resourceGroups/shared/providers/x/a",
                resource_name="a",
                resource_group="shared",
                tags={"application": "Learn App"},
                cost=20,
                currency="CAD",
                pricing_basis="AZURE_ACTUAL",
                shared_scope=True,
            ),
            _row(
                source="azure",
                resource_type="azure_resource",
                resource_id="/subscriptions/s/resourceGroups/shared/providers/x/b",
                resource_name="b",
                resource_group="shared",
                tags={},
                cost=100,
                currency="CAD",
                pricing_basis="AZURE_ACTUAL",
                shared_scope=True,
            ),
        ]
    )
    summary = build_portfolio(evidence)[0]
    ledger_totals = {
        (row["pricing_basis"], row["currency"], row["amount"])
        for row in summary["ledgers"]
    }
    assert ledger_totals == {
        ("AZURE_ACTUAL", "CAD", 20.0),
        ("DATABRICKS_LIST", "USD", 5.0),
    }
    assert not any("total" in key for key in summary)

    profile = build_profile(
        "LEARN APP", evidence, days=30, today=date(2026, 7, 25)
    )
    assert profile is not None
    assert profile["period"]["start"] == "2026-06-26"
    assert profile["unallocated"] == [
        {
            "source": "azure",
            "reason": "SHARED_UNALLOCATED:resource-group:shared",
            "cost": 100.0,
            "currency": "CAD",
            "pricing_basis": "AZURE_ACTUAL",
            "row_count": 1,
        }
    ]


def test_cursor_round_trip_and_validation():
    assert decode_cursor(encode_cursor(23)) == 23
    page, cursor = paginate([{"n": n} for n in range(5)], cursor=None, limit=2)
    assert page == [{"n": 0}, {"n": 1}]
    assert decode_cursor(cursor) == 2
    with pytest.raises(ValueError, match="cursor"):
        decode_cursor("not-valid")


def test_application_evidence_tables_are_append_only():
    statements = dict(create_application_table_statements("main", "platform"))
    assert set(statements) == {
        "table main.platform.azure_cost_evidence_snapshots",
        "table main.platform.azure_cost_resource_evidence",
        "table main.platform.azure_cost_tag_evidence",
        "table main.platform.application_resource_bindings",
        "table main.platform.application_binding_snapshots",
        "table main.platform.application_source_health",
    }
    assert all(
        "'delta.appendOnly' = 'true'" in sql for sql in statements.values()
    )


def test_tag_evidence_is_subscription_scoped_and_idempotently_identified():
    kwargs = {
        "workspace_id": "w1",
        "environment": "prod",
        "subscription_id": "sub1",
        "query_start": "2026-07-01",
        "query_end": "2026-07-25",
        "observed_at": "2026-07-25T12:00:00Z",
    }
    source = [
        {
            "usage_date": "2026-07-24",
            "resource_id": "/subscriptions/sub1/resourceGroups/shared/providers/x/a",
            "resource_group": "shared",
            "tag_key": "Application",
            "tag_value": "Learn App",
            "observed_cost": 7.5,
            "currency": "cad",
        }
    ]
    first = prepare_tag_evidence(source, **kwargs)
    second = prepare_tag_evidence(source, **kwargs)
    assert first == second
    assert first[0]["scope_filter"] == "subscription"
    assert first[0]["tag_key"] == "application"
    assert first[0]["currency"] == "CAD"


def test_app_binding_snapshot_is_generic_and_effective_when_observed():
    class Resource:
        def as_dict(self):
            return {
                "name": "training",
                "job": {"id": "job-1", "permission": "CAN_MANAGE_RUN"},
            }

    app = SimpleNamespace(
        id="app-1",
        name="Learn App",
        resources=[Resource()],
    )
    workspace = SimpleNamespace(
        apps=SimpleNamespace(
            list=lambda: [SimpleNamespace(name="Learn App")],
            get=lambda _name: app,
        )
    )
    rows = fetch_application_bindings(
        workspace,
        workspace_id="w1",
        environment="prod",
        observed_at="2026-07-25T12:00:00Z",
    )
    assert {(row["resource_type"], row["resource_id"]) for row in rows} == {
        ("app", "app-1"),
        ("job", "job-1"),
    }
    assert all(row["application_key"] == "learn-app" for row in rows)
    assert all(row["effective_from"] == "2026-07-25T12:00:00Z" for row in rows)
    snapshot = prepare_binding_snapshot(
        rows,
        workspace_id="w1",
        environment="prod",
        observed_at="2026-07-25T12:00:00Z",
    )
    assert snapshot["app_count"] == 1
    assert snapshot["binding_count"] == 2


def test_append_evidence_uses_insert_only_merge(monkeypatch):
    calls = []
    monkeypatch.setattr(
        application_cost,
        "run_query",
        lambda _w, sql, _warehouse, params=None, **_kwargs: (
            calls.append((sql, params)) or []
        ),
    )
    tag_rows = prepare_tag_evidence(
        [
            {
                "usage_date": "2026-07-25",
                "resource_id": "/subscriptions/sub/resourceGroups/shared/providers/x/a",
                "resource_group": "shared",
                "tag_key": "application",
                "tag_value": "learn-app",
                "observed_cost": 2,
                "currency": "CAD",
            }
        ],
        workspace_id="w1",
        environment="prod",
        subscription_id="sub",
        query_start="2026-07-25",
        query_end="2026-07-25",
        observed_at="2026-07-25T12:00:00Z",
    )
    health = prepare_source_health(
        workspace_id="w1",
        environment="prod",
        source="azure_cost_tags",
        status="healthy",
        notes="subscription tag evidence available",
        observed_at="2026-07-25T12:00:00Z",
    )
    counts = append_application_evidence(
        object(),
        "wh",
        "main",
        "platform",
        tag_rows=tag_rows,
        health_rows=[health],
    )
    assert counts == {
        "azure_cost_evidence_snapshots": 0,
        "azure_cost_resource_evidence": 0,
        "azure_cost_tag_evidence": 1,
        "application_resource_bindings": 0,
        "application_binding_snapshots": 0,
        "application_source_health": 1,
    }
    assert len(calls) == 2
    assert all("WHEN NOT MATCHED THEN INSERT" in sql for sql, _params in calls)
    assert all("WHEN MATCHED" not in sql for sql, _params in calls)


def test_azure_tag_actuals_collapse_once_and_keep_untagged_pool(monkeypatch):
    common = {
        "workspace_id": "w1",
        "environment": "prod",
        "subscription_id": "sub",
        "scope_filter": "subscription",
        "usage_date": "2026-07-25",
        "currency": "CAD",
        "observed_at": "2026-07-26T00:00:00Z",
        "snapshot_id": "snapshot-1",
    }
    resource_rows = [
        {
            **common,
            "evidence_id": f"baseline-{name}",
            "resource_id": (
                f"/subscriptions/sub/resourceGroups/shared/providers/x/{name}"
            ),
            "resource_group": "shared",
            "resource_name": name,
            "resource_type": "x",
            "service": "Azure",
            "cost": cost,
        }
        for name, cost in (("a", 12), ("b", 5), ("c", 10))
    ]
    tag_rows = [
        # The precedence query supplies cost, while a lower key supplies identity.
        {
            **common,
            "resource_id": "/subscriptions/sub/resourceGroups/shared/providers/x/a",
            "resource_group": "shared",
            "tag_key": "application",
            "tag_value": "",
            "observed_cost": 12,
            "evidence_id": "tag-a-application",
        },
        {
            **common,
            "resource_id": "/subscriptions/sub/resourceGroups/shared/providers/x/a",
            "resource_group": "shared",
            "tag_key": "app",
            "tag_value": "Learn App",
            "observed_cost": 12,
            "evidence_id": "tag-a-app",
        },
        {
            **common,
            "resource_id": "/subscriptions/sub/resourceGroups/shared/providers/x/a",
            "resource_group": "shared",
            "tag_key": "project",
            "tag_value": "",
            "observed_cost": 12,
            "evidence_id": "tag-a-project",
        },
        # Empty values remain one shared/unallocated subscription ledger row.
        {
            **common,
            "resource_id": "/subscriptions/sub/resourceGroups/shared/providers/x/b",
            "resource_group": "shared",
            "tag_key": "application",
            "tag_value": "",
            "observed_cost": 5,
            "evidence_id": "tag-b-application",
        },
        {
            **common,
            "resource_id": "/subscriptions/sub/resourceGroups/shared/providers/x/b",
            "resource_group": "shared",
            "tag_key": "app",
            "tag_value": "",
            "observed_cost": 5,
            "evidence_id": "tag-b-app",
        },
        {
            **common,
            "resource_id": "/subscriptions/sub/resourceGroups/shared/providers/x/b",
            "resource_group": "shared",
            "tag_key": "project",
            "tag_value": "",
            "observed_cost": 5,
            "evidence_id": "tag-b-project",
        },
        # Multiple accepted values collapse to one conflict row; cost comes
        # only from the chosen key query and is not repeated per value/key.
        {
            **common,
            "resource_id": "/subscriptions/sub/resourceGroups/shared/providers/x/c",
            "resource_group": "shared",
            "tag_key": "application",
            "tag_value": "Learn App",
            "observed_cost": 4,
            "evidence_id": "tag-c-application-learn",
        },
        {
            **common,
            "resource_id": "/subscriptions/sub/resourceGroups/shared/providers/x/c",
            "resource_group": "shared",
            "tag_key": "application",
            "tag_value": "Other App",
            "observed_cost": 6,
            "evidence_id": "tag-c-application-other",
        },
        {
            **common,
            "resource_id": "/subscriptions/sub/resourceGroups/shared/providers/x/c",
            "resource_group": "shared",
            "tag_key": "app",
            "tag_value": "Learn App",
            "observed_cost": 10,
            "evidence_id": "tag-c-app",
        },
        {
            **common,
            "resource_id": "/subscriptions/sub/resourceGroups/shared/providers/x/c",
            "resource_group": "shared",
            "tag_key": "project",
            "tag_value": "Learn App",
            "observed_cost": 10,
            "evidence_id": "tag-c-project",
        },
    ]

    def fake_query(_w, sql, _warehouse, params=None, **_kwargs):
        del params
        if "system.billing.usage" in sql:
            return []
        if "application_resource_bindings" in sql:
            return []
        if "azure_cost_resource_evidence e" in sql:
            return resource_rows
        if "azure_cost_tag_evidence" in sql:
            return tag_rows
        if "application_source_health" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(application_cost, "run_query", fake_query)
    evidence, _health = read_application_evidence(
        object(),
        "wh",
        "main",
        "platform",
        workspace_id="w1",
        environment="prod",
        subscription_id="sub",
        days=30,
    )
    assert len(evidence) == 3
    by_resource = {row["resource_id"].rsplit("/", 1)[-1]: row for row in evidence}
    assert by_resource["a"]["application_key"] == "learn-app"
    assert by_resource["a"]["cost"] == 12
    assert by_resource["a"]["snapshot_id"] == "snapshot-1"
    assert set(by_resource["a"]["evidence_refs"]) == {
        "baseline-a",
        "tag-a-application",
        "tag-a-app",
        "tag-a-project",
    }
    assert by_resource["b"]["attribution_method"] == "SHARED_UNALLOCATED"
    assert by_resource["b"]["cost"] == 5
    assert by_resource["c"]["attribution_method"] == "CONFLICT"
    assert by_resource["c"]["conflict_values"] == ["learn-app", "other-app"]
    assert by_resource["c"]["cost"] == 10


def test_azure_reads_exclude_old_scopes():
    detail_sql = read_azure_rows_sql("main", "platform")
    assert "INNER JOIN current_scope" in detail_sql
    assert "c.scope_filter = s.scope_filter" in detail_sql

    tag_sql = read_azure_tag_rows_sql("main", "platform")
    assert "s.subscription_id = :subscription_id" in tag_sql
    assert "s.scope_filter = 'subscription'" in tag_sql
    assert "usage_date >= DATE_SUB(CURRENT_DATE(), :days)" in tag_sql
    assert "usage_date <= CURRENT_DATE()" in tag_sql
    assert "current_scope" not in tag_sql


def test_lakebase_bindings_use_stable_billing_aliases():
    class Resource:
        def as_dict(self):
            return {
                "name": "lakebase",
                "postgres": {
                    "database": "projects/p/branches/main/databases/db",
                    "branch": "projects/p/branches/main",
                    "permission": "CAN_CONNECT",
                },
            }

    postgres = SimpleNamespace(
        get_project=lambda _name: SimpleNamespace(project_id="pid", uid="puid"),
        get_branch=lambda _name: SimpleNamespace(branch_id="bid", uid="buid"),
        get_database=lambda _name: SimpleNamespace(database_id="did"),
    )
    app = SimpleNamespace(id="app-1", name="Lake App", resources=[Resource()])
    workspace = SimpleNamespace(
        apps=SimpleNamespace(
            list=lambda: [SimpleNamespace(name="Lake App")],
            get=lambda _name: app,
        ),
        postgres=postgres,
    )
    diagnostics = []
    bindings = fetch_application_bindings(
        workspace,
        workspace_id="w1",
        environment="prod",
        observed_at="2026-07-25T00:00:00Z",
        diagnostics=diagnostics,
    )
    database_ids = {
        row["resource_id"] for row in bindings if row["resource_type"] == "database"
    }
    assert database_ids == {"bid", "buid", "did"}
    assert diagnostics == []


def test_lakebase_alias_failure_is_diagnostic_and_never_uses_path_as_exact():
    class Resource:
        def as_dict(self):
            return {
                "name": "lakebase",
                "postgres": {
                    "database": "projects/p/branches/main/databases/db",
                    "permission": "CAN_CONNECT",
                },
            }

    app = SimpleNamespace(id="app-1", name="Lake App", resources=[Resource()])
    workspace = SimpleNamespace(
        apps=SimpleNamespace(
            list=lambda: [SimpleNamespace(name="Lake App")],
            get=lambda _name: app,
        ),
        postgres=SimpleNamespace(
            get_branch=lambda _name: (_ for _ in ()).throw(PermissionError())
        ),
    )
    diagnostics = []
    bindings = fetch_application_bindings(
        workspace,
        workspace_id="w1",
        environment="prod",
        observed_at="2026-07-25T00:00:00Z",
        diagnostics=diagnostics,
    )
    assert not any(row["resource_type"] == "database" for row in bindings)
    assert diagnostics == ["Lake App/lakebase: PermissionError"]


def test_lakebase_branch_identity_never_falls_back_to_shared_project():
    bindings = [
        {
            "evidence_id": "project-binding",
            "resource_type": "database",
            "resource_id": "project-1",
            "raw_application": "Learn App",
            "effective_from": "2026-07-20T00:00:00Z",
        }
    ]
    [resolved] = resolve_evidence_rows(
        [
            _row(
                usage_date="2026-07-21",
                resource_type="database",
                resource_id="branch-other",
                resource_aliases_json=(
                    '{"project_id":"project-1","branch_id":"branch-other"}'
                ),
            )
        ],
        bindings=bindings,
    )
    assert resolved["attribution_method"] == "UNATTRIBUTED"


def test_latest_binding_expires_when_daily_observation_stops():
    bindings = [
        {
            "evidence_id": "binding-1",
            "resource_type": "job",
            "resource_id": "j1",
            "raw_application": "Learn App",
            "effective_from": "2026-07-20T00:00:00Z",
        }
    ]
    rows = resolve_evidence_rows(
        [
            _row(usage_date="2026-07-21"),
            _row(usage_date="2026-07-22"),
        ],
        bindings=bindings,
    )
    assert rows[0]["attribution_method"] == "DIRECT_RESOURCE"
    assert set(rows[0]["evidence_refs"]) == {
        "binding-1",
        rows[0]["evidence_id"],
    }
    assert rows[1]["attribution_method"] == "UNATTRIBUTED"


def test_snapshot_reads_bound_exploded_dates_to_requested_window():
    for sql in (
        read_azure_resource_evidence_sql("main", "platform"),
        read_azure_tag_rows_sql("main", "platform"),
    ):
        assert "usage_date >= DATE_SUB(CURRENT_DATE(), :days)" in sql
        assert "usage_date <= CURRENT_DATE()" in sql
        assert "PARTITION BY usage_date" in sql
        assert "e.snapshot_id = s.snapshot_id" in sql


def test_snapshot_attempts_and_tag_restatements_remain_distinct():
    base = {
        "workspace_id": "w1",
        "environment": "prod",
        "subscription_id": "sub",
        "query_start": "2026-07-25",
        "query_end": "2026-07-25",
    }
    first_snapshot = azure_evidence_snapshot_id(
        **base,
        observation_id="job:1:run:2:observed:2026-07-25T01:00:00Z",
    )
    retry_snapshot = azure_evidence_snapshot_id(
        **base,
        observation_id="job:1:run:2:observed:2026-07-25T01:01:00Z",
    )
    assert first_snapshot != retry_snapshot

    evidence_ids = []
    for snapshot_id, value, observed_at in (
        (first_snapshot, "Learn App", "2026-07-25T01:00:00Z"),
        (retry_snapshot, "", "2026-07-25T01:01:00Z"),
        ("snapshot-third", "Learn App", "2026-07-25T01:02:00Z"),
    ):
        [row] = prepare_tag_evidence(
            [
                {
                    "usage_date": "2026-07-25",
                    "resource_id": "/subscriptions/sub/resourceGroups/rg/providers/x/a",
                    "tag_key": "application",
                    "tag_value": value,
                    "observed_cost": 10,
                    "currency": "CAD",
                }
            ],
            **base,
            snapshot_id=snapshot_id,
            observed_at=observed_at,
        )
        evidence_ids.append(row["evidence_id"])
    assert len(set(evidence_ids)) == 3


def test_partial_tag_cost_slice_is_conflict_and_preserves_raw_values(monkeypatch):
    common = {
        "workspace_id": "w1",
        "environment": "prod",
        "subscription_id": "sub",
        "scope_filter": "subscription",
        "snapshot_id": "s1",
        "usage_date": "2026-07-25",
        "resource_id": "/subscriptions/sub/resourceGroups/shared/providers/x/a",
        "resource_group": "shared",
        "currency": "CAD",
        "observed_at": "2026-07-26T00:00:00Z",
    }
    baseline = [
        {
            **common,
            "evidence_id": "baseline",
            "resource_name": "a",
            "resource_type": "x",
            "service": "Azure",
            "cost": 10,
        }
    ]
    tags = [
        {
            **common,
            "evidence_id": evidence_id,
            "tag_key": key,
            "tag_value": value,
            "observed_cost": cost,
        }
        for evidence_id, key, value, cost in (
            ("tag-app-part", "application", "Learn App", 2),
            ("tag-app-blank", "application", "", 8),
            ("tag-lower", "app", "Learn App", 10),
            ("tag-project", "project", "Learn App", 10),
        )
    ]

    def fake_query(_w, sql, _warehouse, params=None, **_kwargs):
        del params
        if "system.billing.usage" in sql:
            return []
        if "application_resource_bindings" in sql:
            return []
        if "azure_cost_resource_evidence e" in sql:
            return baseline
        if "azure_cost_tag_evidence" in sql:
            return tags
        if "application_source_health" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(application_cost, "run_query", fake_query)
    evidence, _health = read_application_evidence(
        object(),
        "wh",
        "main",
        "platform",
        workspace_id="w1",
        environment="prod",
        subscription_id="sub",
        days=30,
    )
    assert evidence[0]["attribution_method"] == "CONFLICT"
    assert evidence[0]["application_key"] is None
    assert evidence[0]["cost"] == 10
    assert {
        (row["tag_key"], row["tag_value"], row["observed_cost"])
        for row in evidence[0]["tag_observations"]
    } >= {
        ("application", "Learn App", 2.0),
        ("application", "", 8.0),
    }


def test_metadata_rows_with_matching_tags_have_healthy_tag_alignment():
    evidence = resolve_evidence_rows(
        [_row(metadata_application="Learn App", tags={"application": "learn app"})]
    )
    summary = build_portfolio(evidence)[0]
    assert summary["tag_health"] == {
        "status": "matched",
        "matched": 1,
        "missing": 0,
        "conflicts": 0,
    }


def test_each_ledger_has_its_own_trend_scope_and_health():
    evidence = resolve_evidence_rows(
        [
            _row(
                usage_date="2026-07-20",
                metadata_application="Learn App",
                cost=10,
            ),
            _row(
                usage_date="2026-07-21",
                metadata_application="Learn App",
                cost=20,
            ),
        ]
    )
    [summary] = build_portfolio(
        evidence,
        source_health=[
            {
                "source": "databricks_billing",
                "status": "healthy",
            }
        ],
    )
    [ledger] = summary["ledgers"]
    assert ledger["trend_pct"] == 100.0
    assert ledger["scope"] == ["workspace:w1"]
    assert ledger["trusted"] is True


def test_stale_source_health_is_not_treated_as_trustworthy():
    current = datetime(2026, 7, 25, tzinfo=UTC)
    health = application_cost._normalize_source_health(
        {
            "source": "azure_cost_resources",
            "status": "healthy",
            "subscription_id": "sub",
            "scope_filter": "subscription",
            "last_success_at": (current - timedelta(days=4)).isoformat(),
            "coverage_end": "2026-07-21",
            "notes": "last sync succeeded",
        },
        current_time=current,
    )
    assert health["status"] == "stale"
    assert health["scope"] == "subscription:sub"
    assert health["freshness"] == "2026-07-21T00:00:00+00:00"


@pytest.mark.parametrize(
    ("status", "ledger_status"),
    (("stale", "partial"), ("truncated", "unavailable")),
)
def test_stale_or_truncated_source_excludes_current_exact_claim(
    status,
    ledger_status,
):
    evidence = resolve_evidence_rows(
        [
            _row(
                source="azure",
                tags={"application": "Learn App"},
                currency="CAD",
                pricing_basis="AZURE_ACTUAL",
                shared_scope=True,
            )
        ]
    )
    [summary] = build_portfolio(
        evidence,
        source_health=[
            {"source": "azure_cost_resources", "status": status},
            {"source": "azure_cost_tags", "status": "healthy"},
        ],
    )

    [ledger] = summary["ledgers"]
    assert ledger["amount"] == 4.0
    assert ledger["status"] == ledger_status
    assert ledger["trusted"] is False
    assert summary["coverage_pct"] is None


def test_resolved_raw_evidence_carries_job_run_and_trigger_attestation():
    [row] = resolve_evidence_rows(
        [
            _row(
                metadata_application="Learn App",
                snapshot_id="snapshot-1",
                job_id="11",
                run_id="22",
                trigger_type="PERIODIC",
            )
        ]
    )

    assert (
        row["snapshot_id"],
        row["job_id"],
        row["run_id"],
        row["trigger_type"],
    ) == ("snapshot-1", "11", "22", "PERIODIC")


def test_manifests_store_explicit_attestation_and_grants_are_least_privilege():
    snapshot = prepare_azure_evidence_snapshot(
        snapshot_id="s1",
        workspace_id="w1",
        environment="prod",
        subscription_id="sub",
        query_start="2026-07-25",
        query_end="2026-07-25",
        baseline_status="complete",
        tag_status="complete",
        tag_keys=("application", "app", "project"),
        row_count=2,
        job_id="11",
        run_id="22",
        trigger_type="FILE_ARRIVAL",
        observed_at="2026-07-25T00:00:00Z",
    )
    assert (snapshot["job_id"], snapshot["run_id"], snapshot["trigger_type"]) == (
        "11",
        "22",
        "FILE_ARRIVAL",
    )
    [resource] = prepare_azure_resource_evidence(
        [
            {
                "usage_date": "2026-07-25",
                "resource_id": "/subscriptions/sub/resourceGroups/rg/providers/x/a",
                "cost": 3,
                "currency": "CAD",
            }
        ],
        workspace_id="w1",
        environment="prod",
        subscription_id="sub",
        query_start="2026-07-25",
        query_end="2026-07-25",
        snapshot_id="s1",
        observed_at="2026-07-25T00:00:00Z",
    )
    assert resource["snapshot_id"] == "s1"
    grants = application_table_grant_statements(
        "main",
        "platform",
        app_service_principal="app-sp",
        runtime_executor_service_principal="runtime-sp",
    )
    sql = "\n".join(statement for _description, statement in grants)
    assert sql.count("TO `app-sp`") == 6
    assert sql.count("TO `runtime-sp`") == 6
    assert "GRANT SELECT, MODIFY" in sql
    assert "MODIFY ON TABLE main.platform" in sql


def test_application_findings_share_resolved_evidence():
    evidence = resolve_evidence_rows(
        [
            _row(
                source="azure",
                resource_group="shared",
                tags={"application": "Learn App"},
                pricing_basis="AZURE_ACTUAL",
                currency="CAD",
                shared_scope=True,
            ),
            _row(
                source="azure",
                resource_id="untagged",
                resource_group="shared",
                tags={},
                pricing_basis="AZURE_ACTUAL",
                currency="CAD",
                shared_scope=True,
            ),
        ]
    )
    findings = application_cost.classify_application_findings(
        evidence,
        [
            {
                "source": "azure_cost_tags",
                "status": "partial",
                "notes": "one tag query failed",
            }
        ],
    )
    assert len(findings["governance/application-tag-missing"]) == 1
    assert len(findings["governance/application-source-health"]) == 1

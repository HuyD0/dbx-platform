"""Exact canonical resource-identifier correlation tests."""

from dbx_platform.resource_identifiers import (
    extract_resource_ids,
    matching_resource_ids,
    parse_resource_ids,
)


def test_nested_resource_identifiers_are_normalized_without_fuzzy_matching():
    targets = [
        {"cluster_id": " cluster-1 "},
        {"job": {"job_id": 42}},
        {"metadata": {"description": "not-an-identifier"}},
        {"protected": True},
    ]

    assert extract_resource_ids(targets) == {"cluster-1", "42"}
    assert parse_resource_ids('[{"resource_id":"42"},{"name":"policy-a"}]') == {
        "42",
        "policy-a",
    }
    assert matching_resource_ids(targets, [{"resource_id": "42"}]) == {"42"}
    assert matching_resource_ids(targets, [{"resource_id": "cluster-10"}]) == set()


def test_non_json_affected_resource_value_remains_an_exact_identifier():
    assert parse_resource_ids("warehouse-1") == {"warehouse-1"}
    assert parse_resource_ids("") == set()

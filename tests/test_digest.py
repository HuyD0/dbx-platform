import json

from dbx_platform.digest import build_digest_prompt, flatten_findings

FINDINGS = {
    "cost/cluster-utilization": [
        {"cluster_id": "c-1", "cluster_name": "etl", "creator": "a@example.com",
         "list_cost_usd": 900.0, "reason": "p95 CPU 5%", "action": "downsize"}
    ],
    "security/token-audit": [
        {"token_id": "t-1", "owner": "b@example.com", "reason": "over age",
         "action": "revoke"}
    ],
    "ml/endpoint-audit": [],
}


def test_prompt_includes_counts_window_and_findings():
    prompt = build_digest_prompt(FINDINGS, {}, days=30)
    assert "last 30 days" in prompt
    assert "Total findings: 2" in prompt
    assert "cost/cluster-utilization" in prompt
    assert "b@example.com" in prompt


def test_prompt_omits_empty_checks_from_payload():
    prompt = build_digest_prompt(FINDINGS, {}, days=30)
    payload = prompt[prompt.index("{"):]
    assert "ml/endpoint-audit" not in payload


def test_prompt_is_deterministic():
    assert build_digest_prompt(FINDINGS, {}, 30) == build_digest_prompt(FINDINGS, {}, 30)


def test_prompt_names_skipped_checks():
    prompt = build_digest_prompt({}, {"cost/failed-run-waste": "no warehouse"}, 30)
    assert "could not run" in prompt
    assert "cost/failed-run-waste" in prompt


def test_prompt_without_skips_has_no_skip_note():
    assert "could not run" not in build_digest_prompt(FINDINGS, {}, 30)


def test_flatten_findings_rows_carry_area_check_and_resource():
    rows = flatten_findings(FINDINGS)
    assert len(rows) == 2
    by_area = {r["area"]: r for r in rows}
    assert by_area["cost"]["check_name"] == "cluster-utilization"
    assert by_area["cost"]["resource"] == "etl"
    assert by_area["security"]["action"] == "revoke"
    # details round-trips as JSON
    assert json.loads(by_area["cost"]["details"])["list_cost_usd"] == 900.0


def test_flatten_findings_empty():
    assert flatten_findings({}) == []
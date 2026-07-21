from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TAGS = {"team", "project"}


def _bundle_resources() -> list[tuple[str, str, dict]]:
    resources = []
    for resource_file in (REPO_ROOT / "resources").glob("*.yml"):
        document = yaml.safe_load(resource_file.read_text()) or {}
        bundle_resources = document.get("resources", {})
        for name, job in (bundle_resources.get("jobs") or {}).items():
            resources.append(("job", name, job.get("tags") or {}))
        for name, warehouse in (bundle_resources.get("sql_warehouses") or {}).items():
            custom_tags = {
                item["key"]: item["value"]
                for item in (warehouse.get("tags") or {}).get("custom_tags", [])
            }
            resources.append(("sql_warehouse", name, custom_tags))
    return resources


def test_cost_bearing_bundle_resources_have_team_and_project_tags():
    resources = _bundle_resources()
    assert resources
    missing = [
        f"{kind}:{name}:{sorted(REQUIRED_TAGS - tags.keys())}"
        for kind, name, tags in resources
        if not REQUIRED_TAGS <= tags.keys()
    ]
    assert missing == []


def test_platform_resource_tag_values_are_consistent():
    for _kind, _name, tags in _bundle_resources():
        assert tags["team"] == "platform"
        assert tags["project"] == "dbx-platform"

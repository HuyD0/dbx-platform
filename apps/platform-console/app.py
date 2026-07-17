"""Platform Console — findings overview.

Reads the platform_findings table the scheduled digest job populates, with
per-area "run fresh" buttons that execute the same fetch+classify code the
CLI and jobs use.
"""

from __future__ import annotations

import streamlit as st
from console_common import (
    client,
    findings_table,
    now_ms,
    safety_note,
    settings,
    show_rows,
    sql,
    warehouse_id,
)

from dbx_platform import cost, governance, housekeeping, ml

st.set_page_config(page_title="Platform Console", page_icon="🛠️", layout="wide")
st.title("Platform Console")
st.caption("dbx-platform findings, AI/ML cost, digests and job kick-off — "
           "one code path with the CLI and scheduled jobs.")
safety_note()


def _fresh_checks() -> dict:
    w, s = client(), settings()
    return {
        "housekeeping / stale-clusters": lambda: housekeeping.classify_clusters(
            housekeeping.fetch_clusters(w), now_ms(), s.stale_cluster_days,
            s.max_uptime_hours),
        "housekeeping / jobs-on-all-purpose": lambda: housekeeping.find_jobs_on_all_purpose(
            housekeeping.fetch_jobs_with_clusters(w), s.allpurpose_fixed_workers_max),
        "governance / tag-compliance": lambda: governance.find_missing_tags(
            governance.fetch_taggable_resources(w), s.required_tag_list()),
        "ml / endpoint-audit": lambda: ml.classify_serving_endpoints(
            ml.fetch_serving_endpoints(w), now_ms(), s.serving_failed_grace_hours),
        "ml / vector-search-audit": lambda: ml.find_vector_search_findings(
            ml.fetch_vector_search(w), now_ms(), s.vector_search_grace_hours),
        "cost / cluster-utilization": lambda: cost.classify_cluster_utilization(
            cost.cluster_utilization(w, warehouse_id(), s.lookback_days),
            s.util_cpu_threshold_pct, s.util_mem_threshold_pct),
    }


tab_stored, tab_fresh = st.tabs(["Latest stored findings", "Run a check now"])

with tab_stored:
    try:
        rows = sql(
            f"SELECT run_ts, area, check_name, resource, reason, action "
            f"FROM {findings_table()} "
            f"WHERE run_ts = (SELECT MAX(run_ts) FROM {findings_table()}) "
            f"ORDER BY area, check_name"
        )
    except Exception as e:  # table missing until 'dashboards setup' has run
        rows = []
        st.warning(f"No stored findings yet ({e}). "
                   "Run `dbx-platform dashboards setup`, then the digest job.")
    if rows:
        areas = sorted({r["area"] for r in rows})
        picked = st.multiselect("Areas", areas, default=areas)
        show_rows([r for r in rows if r["area"] in picked])
    else:
        show_rows(rows, "No stored findings — the platform is clean or the "
                        "digest job has not run yet.")

with tab_fresh:
    checks = _fresh_checks()
    choice = st.selectbox("Check", list(checks))
    if st.button("Run"):
        with st.spinner(f"Running {choice}…"):
            try:
                show_rows(checks[choice]())
            except Exception as e:
                st.error(f"{choice} failed: {e}")

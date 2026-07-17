"""Actions view: kick off the dbx-platform report jobs and watch run status.

Deliberately limited: the app's identity holds run permission on the
report-only jobs; nothing here can pass --apply. Destructive actions remain
CLI-with-confirmation.
"""

from __future__ import annotations

import streamlit as st
from console_common import client, safety_note

st.title("Actions")
safety_note()
st.markdown(
    "Kick off a **report-only** job below. Scheduled parameters never include "
    "`--apply`; remediation is done via the CLI or a reviewed pull request."
)

JOB_MARKER = "dbx-platform"


@st.cache_data(ttl=120)
def platform_jobs() -> list[dict]:
    out = []
    for j in client().jobs.list():
        name = j.settings.name if j.settings else ""
        if JOB_MARKER in (name or ""):
            out.append({"job_id": j.job_id, "name": name})
    return sorted(out, key=lambda x: x["name"])


jobs = platform_jobs()
if not jobs:
    st.info("No [dbx-platform] jobs visible to the app's identity — deploy the "
            "bundle and grant the app CAN_MANAGE_RUN (see docs/runbook.md).")

for job in jobs:
    col_name, col_run = st.columns([4, 1])
    col_name.write(f"**{job['name']}**  (`{job['job_id']}`)")
    if col_run.button("Run now", key=f"run-{job['job_id']}"):
        run = client().jobs.run_now(job_id=job["job_id"])
        st.toast(f"Started run {run.run_id} of {job['name']}")

st.divider()
st.subheader("Recent runs")
picked = st.selectbox("Job", jobs, format_func=lambda j: j["name"]) if jobs else None
if picked:
    runs = client().jobs.list_runs(job_id=picked["job_id"], limit=5)
    rows = [
        {
            "run_id": r.run_id,
            "state": (r.state.life_cycle_state.value
                      if r.state and r.state.life_cycle_state else ""),
            "result": (r.state.result_state.value
                       if r.state and r.state.result_state else ""),
            "started": r.start_time,
        }
        for r in runs
    ]
    import pandas as pd

    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

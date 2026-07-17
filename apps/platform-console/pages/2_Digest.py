"""Digest view: AI-written platform health summaries, with on-demand refresh."""

from __future__ import annotations

import streamlit as st
from console_common import client, digest_table, now_ms, safety_note, settings, sql, warehouse_id

from dbx_platform import digest

st.title("AI digest")
safety_note()

try:
    rows = sql(
        f"SELECT run_ts, days, model, digest FROM {digest_table()} "
        f"ORDER BY run_ts DESC LIMIT 5"
    )
except Exception as e:
    rows = []
    st.warning(f"No stored digests yet ({e}). "
               "Run `dbx-platform dashboards setup`, then the digest job.")

for r in rows:
    with st.expander(f"{r['run_ts']} — last {r['days']}d — {r['model']}",
                     expanded=(r is rows[0])):
        st.markdown(r["digest"] or "_empty digest_")
if not rows:
    st.info("No digests stored. Generate one below or wait for the weekly job.")

st.divider()
if st.button("Generate a fresh digest"):
    s = settings()
    with st.spinner("Collecting findings and querying the model…"):
        findings, skipped = digest.collect_findings(
            client(), s, warehouse_id(), now_ms(), s.lookback_days
        )
        prompt = digest.build_digest_prompt(findings, skipped, s.lookback_days)
        try:
            summary = digest.summarize(client(), warehouse_id(), s.digest_model, prompt)
            st.markdown(summary)
            digest.store_digest(
                client(), warehouse_id(), s.dashboard_catalog, s.dashboard_schema,
                s.lookback_days, s.digest_model, summary, findings,
            )
            st.caption("Stored to the digest table.")
        except Exception as e:
            st.error(f"ai_query unavailable ({e}) — raw findings below.")
            st.json(findings)

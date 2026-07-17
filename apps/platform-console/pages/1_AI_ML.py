"""AI/ML view: serving endpoint health, spend, token usage, GPU share."""

from __future__ import annotations

import streamlit as st
from console_common import client, now_ms, safety_note, settings, show_rows, warehouse_id

from dbx_platform import ml
from dbx_platform.system_tables import SystemTablesUnavailableError

st.title("AI/ML")
safety_note()

days = st.slider("Window (days)", 7, 90, 30)

st.subheader("Serving endpoint health")
if st.button("Audit endpoints now"):
    with st.spinner("Fetching serving endpoints…"):
        endpoints = ml.fetch_serving_endpoints(client())
        findings = ml.classify_serving_endpoints(
            endpoints, now_ms(), settings().serving_failed_grace_hours
        )
        st.metric("Endpoints", len(endpoints))
        show_rows(findings, "All serving endpoints look healthy.")

st.subheader("AI/ML spend by product / SKU / endpoint")
try:
    show_rows(ml.serving_cost(client(), warehouse_id(), days), "No AI/ML spend found.")
except (SystemTablesUnavailableError, ValueError) as e:
    st.info(f"skipped: {e}")

st.subheader("Token usage by endpoint / requester")
try:
    show_rows(
        ml.endpoint_token_usage(client(), warehouse_id(), days),
        "No token usage recorded.",
    )
except (SystemTablesUnavailableError, ValueError) as e:
    st.info(f"skipped: {e}")

st.subheader("GPU spend share")
try:
    show_rows(ml.gpu_spend(client(), warehouse_id(), days), "No cluster spend found.")
except (SystemTablesUnavailableError, ValueError) as e:
    st.info(f"skipped: {e}")

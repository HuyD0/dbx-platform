"""Azure Cost view: the ingested Azure bill, the ML forecast, and the
forecast monitor's health — presentation only, same code path as the CLI
(`dbx-platform azure-cost ...` / `dbx-platform forecast ...`) and jobs."""

from __future__ import annotations

import streamlit as st
from console_common import (
    client,
    findings_table,
    safety_note,
    settings,
    show_rows,
    sql,
    warehouse_id,
)

from dbx_platform import azure_cost

st.title("Azure Cost")
st.caption("Resource-group-scoped Azure bill (Cost Management Query API) next "
           "to Databricks-side cost checks, plus the ML forecast from the "
           "@champion model.")
safety_note()

s = settings()
fq = f"{s.dashboard_catalog}.{s.dashboard_schema}"
workspace_id = str(client().get_workspace_id())

tab_bill, tab_forecast, tab_health = st.tabs(
    ["Azure bill", "Forecast", "Forecast health"]
)

with tab_bill:
    days = st.slider("Window (days)", 7, 180, 30)
    try:
        daily = azure_cost.fetch_daily_buckets(
            client(),
            warehouse_id(),
            s.dashboard_catalog,
            s.dashboard_schema,
            days,
            workspace_id=workspace_id,
            environment=s.environment,
        )
    except Exception as e:  # table missing until the pull job has run
        daily = []
        st.warning(f"No Azure bill data yet ({e}). Run the azure-cost-pull job "
                   "or `dbx-platform azure-cost pull` first.")
    if daily:
        import pandas as pd

        frame = pd.DataFrame(daily)
        frame["cost"] = pd.to_numeric(frame["cost"], errors="coerce")
        st.metric(f"Spend, last {days}d", f"{frame['cost'].sum():,.2f}")
        pivot = frame.pivot_table(index="usage_date", columns="service_bucket",
                                  values="cost", aggfunc="sum").fillna(0)
        st.bar_chart(pivot)
        by = st.selectbox("Break down by", ["bucket", "service", "resource-group"])
        show_rows(
            azure_cost.report(
                client(),
                warehouse_id(),
                s.dashboard_catalog,
                s.dashboard_schema,
                by,
                days,
                workspace_id=workspace_id,
                environment=s.environment,
            )
        )
    st.subheader("Spend spikes (fresh check)")
    if st.button("Run spike check now"):
        with st.spinner("Classifying per-bucket spend…"):
            rows = azure_cost.fetch_daily_buckets(
                client(),
                warehouse_id(),
                s.dashboard_catalog,
                s.dashboard_schema,
                14,
                workspace_id=workspace_id,
                environment=s.environment,
            )
            findings = azure_cost.classify_azure_spend(
                rows, s.azure_spike_pct, s.azure_spike_min_cost
            )
            show_rows(findings, "No spend spikes above threshold.")

with tab_forecast:
    try:
        forecast = sql(
            "SELECT target_date, series, p10, p50, p90, model_version "
            f"FROM {fq}.cost_forecasts "
            f"WHERE run_date = (SELECT MAX(run_date) FROM {fq}.cost_forecasts) "
            "ORDER BY series, target_date"
        )
    except Exception as e:  # table missing until the daily forecast job has run
        forecast = []
        st.warning(f"No forecasts yet ({e}). Run the cost-forecast-train and "
                   "cost-forecast-daily jobs first.")
    if forecast:
        import pandas as pd

        frame = pd.DataFrame(forecast)
        for c in ("p10", "p50", "p90"):
            frame[c] = pd.to_numeric(frame[c], errors="coerce")
        st.caption(f"Latest run — model version {frame['model_version'].iloc[0]} "
                   "(resolved via the @champion alias)")
        total = frame[frame["series"] == "total"].set_index("target_date")
        if not total.empty:
            st.line_chart(total[["p10", "p50", "p90"]])
        picked = st.multiselect(
            "Series", sorted(frame["series"].unique()), default=["total"]
        )
        show_rows(frame[frame["series"].isin(picked)].to_dict("records"))

with tab_health:
    st.caption("Drift + matured-accuracy verdicts written by the forecast "
               "monitor task (area = forecast in platform_findings).")
    try:
        rows = sql(
            "SELECT run_ts, check_name, resource, reason, action "
            f"FROM {findings_table()} WHERE area = 'forecast' "
            "ORDER BY run_ts DESC LIMIT 100"
        )
    except Exception as e:
        rows = []
        st.warning(f"No stored findings yet ({e}). "
                   "Run `dbx-platform dashboards setup`, then the forecast jobs.")
    show_rows(rows, "No forecast monitor findings stored yet.")

"""Shared helpers for the Platform Console app.

Presentation layer only: every check and query comes from the dbx_platform
package (same code path as the CLI and the scheduled jobs). The workspace
client is created lazily inside cached functions so importing this module
never touches the network.
"""

from __future__ import annotations

import os
import time

import streamlit as st

from dbx_platform.client import get_client
from dbx_platform.config import Settings
from dbx_platform.system_tables import run_query


def now_ms() -> int:
    return int(time.time() * 1000)


@st.cache_resource
def client():
    return get_client(None)


@st.cache_resource
def settings() -> Settings:
    return Settings.from_env()


def warehouse_id() -> str:
    return os.environ.get("DBX_PLATFORM_WAREHOUSE_ID", "") or settings().warehouse_id


def findings_table() -> str:
    s = settings()
    return f"{s.dashboard_catalog}.{s.dashboard_schema}.platform_findings"


def digest_table() -> str:
    s = settings()
    return f"{s.dashboard_catalog}.{s.dashboard_schema}.platform_digest"


@st.cache_data(ttl=300)
def sql(query: str) -> list[dict]:
    return run_query(client(), query, warehouse_id())


def show_rows(rows: list[dict], empty_message: str = "No findings.") -> None:
    import pandas as pd

    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.success(empty_message)


def safety_note() -> None:
    st.caption(
        "Report-only surface: this app never mutates workspace resources. "
        "`--apply` actions stay CLI-with-confirmation, per the repo safety model."
    )

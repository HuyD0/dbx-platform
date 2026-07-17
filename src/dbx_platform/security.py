"""Security & audit: PAT token audit and inactive-user report.

Fills two known platform-admin gaps: Databricks has no native PAT expiry
enforcement and no built-in inactive-user report.
"""

from __future__ import annotations

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import load_query, run_query

MS_PER_DAY = 86_400_000


# --- token audit (requires workspace admin) --------------------------------

def fetch_tokens(w: WorkspaceClient) -> list[dict]:
    out = []
    for t in w.token_management.list():
        out.append(
            {
                "token_id": t.token_id,
                "created_by": t.created_by_username or "",
                "comment": t.comment or "",
                "creation_time": t.creation_time or 0,
                # -1 means the token never expires
                "expiry_time": t.expiry_time if t.expiry_time is not None else -1,
            }
        )
    return out


def classify_tokens(
    tokens: list[dict], now_ms: int, max_age_days: int, expiry_warn_days: int
) -> list[dict]:
    """Pure decision logic. Flags: never-expires, over max age, expiring soon."""
    findings = []
    for t in tokens:
        issues = []
        over_age = False
        age_days = (now_ms - t["creation_time"]) / MS_PER_DAY if t["creation_time"] else 0
        if t["expiry_time"] in (-1, 0):
            issues.append("never expires")
        elif 0 < t["expiry_time"] - now_ms <= expiry_warn_days * MS_PER_DAY:
            days_left = (t["expiry_time"] - now_ms) / MS_PER_DAY
            issues.append(f"expires in {days_left:.0f}d — rotate soon")
        if age_days > max_age_days:
            issues.append(f"age {age_days:.0f}d > {max_age_days}d")
            over_age = True
        if issues:
            findings.append(
                {
                    "token_id": t["token_id"],
                    "created_by": t["created_by"],
                    "comment": t["comment"],
                    "age_days": round(age_days),
                    "issues": "; ".join(issues),
                    "over_age": over_age,
                }
            )
    return findings


def revoke_tokens(w: WorkspaceClient, findings: list[dict]) -> list[str]:
    """Revoke only tokens flagged over the age threshold (never touches
    merely-expiring-soon tokens)."""
    done = []
    for f in findings:
        if f["over_age"]:
            w.token_management.delete(token_id=f["token_id"])
            done.append(f"revoked token {f['token_id']} (created by {f['created_by']})")
    return done


# --- inactive users ---------------------------------------------------------

def fetch_workspace_users(w: WorkspaceClient) -> list[dict]:
    return [
        {"user_name": u.user_name or "", "display_name": u.display_name or "",
         "active": u.active is not False}
        for u in w.users.list(attributes="userName,displayName,active")
    ]


def fetch_user_activity(w: WorkspaceClient, warehouse_id: str, days: int) -> list[dict]:
    return run_query(w, load_query("inactive_users"), warehouse_id, {"days": days})


def find_inactive_users(users: list[dict], activity: list[dict], days: int) -> list[dict]:
    """Pure decision logic: active SCIM users with zero audited activity in
    the window. Report-only — deactivation stays a human/IdP decision."""
    seen = {row["email"].lower() for row in activity if row.get("email")}
    return [
        {
            "user_name": u["user_name"],
            "display_name": u["display_name"],
            "reason": f"no audited activity in {days}d",
        }
        for u in users
        if u["active"] and u["user_name"].lower() not in seen
    ]

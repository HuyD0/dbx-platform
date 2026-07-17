from conftest import MS_PER_DAY, days_ago

from dbx_platform.security import classify_tokens, find_inactive_users


def _token(**overrides) -> dict:
    base = {
        "token_id": "t-1",
        "created_by": "someone@example.com",
        "comment": "",
        "creation_time": days_ago(10),
        "expiry_time": days_ago(-30),  # expires 30 days from now
    }
    return {**base, **overrides}


def test_never_expiring_token_flagged(now_ms):
    findings = classify_tokens([_token(expiry_time=-1)], now_ms,
                               max_age_days=90, expiry_warn_days=14)
    assert len(findings) == 1
    assert "never expires" in findings[0]["issues"]
    assert findings[0]["over_age"] is False


def test_old_token_flagged_over_age(now_ms):
    findings = classify_tokens([_token(creation_time=days_ago(91))], now_ms,
                               max_age_days=90, expiry_warn_days=14)
    assert len(findings) == 1
    assert findings[0]["over_age"] is True


def test_young_token_not_flagged(now_ms):
    assert classify_tokens([_token(creation_time=days_ago(10))], now_ms,
                           max_age_days=90, expiry_warn_days=14) == []


def test_token_expiring_soon_gets_rotation_warning(now_ms):
    findings = classify_tokens([_token(expiry_time=now_ms + 7 * MS_PER_DAY)], now_ms,
                               max_age_days=90, expiry_warn_days=14)
    assert len(findings) == 1
    assert "rotate soon" in findings[0]["issues"]
    assert findings[0]["over_age"] is False


def test_inactive_users_anti_join():
    users = [
        {"user_name": "active@example.com", "display_name": "Active", "active": True},
        {"user_name": "idle@example.com", "display_name": "Idle", "active": True},
        {"user_name": "disabled@example.com", "display_name": "Disabled", "active": False},
    ]
    activity = [{"email": "Active@example.com", "last_seen": "2026-07-01", "events": 12}]
    inactive = find_inactive_users(users, activity, days=90)
    assert [u["user_name"] for u in inactive] == ["idle@example.com"]

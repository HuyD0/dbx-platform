"""Tiny TTL cache for slow warehouse/SDK reads.

Per-key locks prevent a stampede of identical warehouse queries when several
browser tabs hit the same endpoint; every cached route accepts ?refresh=true
to bypass the TTL, and responses carry the as-of timestamp so the UI can show
freshness instead of silently serving stale data.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

DEFAULT_TTL_SECONDS = 300

_store: dict[str, tuple[float, Any]] = {}
_locks: dict[str, threading.Lock] = {}
_meta_lock = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _meta_lock:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


def cached(
    key: str,
    loader: Callable[[], Any],
    refresh: bool = False,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> tuple[Any, datetime, bool]:
    """Return (value, as_of, was_cached)."""
    with _lock_for(key):
        now = time.time()
        hit = _store.get(key)
        if hit is not None and not refresh and now - hit[0] < ttl_seconds:
            return hit[1], _dt(hit[0]), True
        value = loader()
        _store[key] = (now, value)
        return value, _dt(now), False


def clear() -> None:
    with _meta_lock:
        _store.clear()


def _dt(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)

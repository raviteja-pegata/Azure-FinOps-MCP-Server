"""TTL cache for expensive Azure Cost Management queries.

Why cache at all?
- Cost Management API has aggressive rate limits (~30 requests/minute
  per tenant). A conversational LLM can easily blow through that by
  re-phrasing the same question.
- Cost data updates at most once per hour. Caching for 15 minutes
  (default) loses almost nothing in freshness.
- Cache is in-memory (process-local). When the server restarts, it's
  gone. No stale-cache bugs to debug.

Usage in tools:
    result = cached("cost_summary", {"scope": scope, ...}, lambda: expensive_call())

The cache key is a SHA-256 hash of the namespace + payload dict, so
identical queries (same scope, same dates, same grouping) share a
cache entry regardless of argument order.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from cachetools import TTLCache

from .config import CACHE_TTL_SECONDS

_cache: TTLCache = TTLCache(maxsize=256, ttl=CACHE_TTL_SECONDS)


def _key(namespace: str, payload: dict[str, Any]) -> str:
    """Build a deterministic cache key from namespace + payload."""
    blob = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(blob.encode()).hexdigest()[:16]
    return f"{namespace}:{digest}"


def cached(namespace: str, payload: dict[str, Any], producer: Callable[[], Any]) -> Any:
    """Return cached value if available, otherwise call producer() and cache it."""
    k = _key(namespace, payload)
    if k in _cache:
        return _cache[k]
    value = producer()
    _cache[k] = value
    return value

"""Configuration for the Azure FinOps MCP server.

Loads settings from environment variables (or .env file via python-dotenv).
Validates at import time so the server fails fast on misconfiguration.

Key design decisions:
- Subscription allowlist is a frozenset (immutable, O(1) lookup).
- resolve_subscription() is the single chokepoint every tool must call
  before making any Azure API call. This prevents the LLM from querying
  subscriptions you didn't explicitly authorize.
- Fail-fast validation at import: if the default subscription isn't in
  the allowlist, the server refuses to start rather than failing later
  with a confusing error mid-conversation.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

# ── Parse the allowlist ──────────────────────────────────────────────────────

_allowed_raw = os.environ.get("AZURE_ALLOWED_SUBSCRIPTIONS", "")
ALLOWED_SUBSCRIPTIONS: frozenset[str] = frozenset(
    s.strip() for s in _allowed_raw.split(",") if s.strip()
)

# ── Default subscription with validation ─────────────────────────────────────

DEFAULT_SUBSCRIPTION: str = os.environ.get("AZURE_DEFAULT_SUBSCRIPTION", "").strip()

if not DEFAULT_SUBSCRIPTION:
    raise RuntimeError(
        "AZURE_DEFAULT_SUBSCRIPTION is required. Set it in your .env file."
    )

if DEFAULT_SUBSCRIPTION not in ALLOWED_SUBSCRIPTIONS:
    raise RuntimeError(
        f"AZURE_DEFAULT_SUBSCRIPTION ({DEFAULT_SUBSCRIPTION}) must also be "
        f"listed in AZURE_ALLOWED_SUBSCRIPTIONS."
    )

# ── Optional settings ────────────────────────────────────────────────────────

CACHE_TTL_SECONDS: int = int(os.environ.get("FINOPS_CACHE_TTL_SECONDS", "900"))
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")


# ── The function every tool calls ────────────────────────────────────────────

def resolve_subscription(subscription_id: str | None) -> str:
    """Validate and resolve the subscription ID to query.

    If None is passed, returns the configured default.
    Raises ValueError if the subscription is not in the allowlist.

    Every tool must call this before making any Azure API call.
    """
    sub = subscription_id or DEFAULT_SUBSCRIPTION
    if sub not in ALLOWED_SUBSCRIPTIONS:
        raise ValueError(
            f"Subscription {sub!r} is not in AZURE_ALLOWED_SUBSCRIPTIONS. "
            f"Allowed: {sorted(ALLOWED_SUBSCRIPTIONS)}"
        )
    return sub

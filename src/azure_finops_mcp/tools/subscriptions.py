"""Subscription discovery tool.

Why this exists:
  With 5+ subscriptions, the LLM can't guess which ID maps to "prod data
  platform." This tool lets it discover allowed subscriptions by name,
  then use the right ID in subsequent cost/optimization calls.

  It's a "tool that enables other tools" — the LLM's typical flow is:
  1. User asks "what did our data platform cost last month?"
  2. LLM calls list_subscriptions() to find the sub named "Prod - Data"
  3. LLM calls get_cost_summary(subscription_id="...") with the matched ID

Design decisions:
  - Only returns subscriptions in the allowlist, not everything in the tenant.
    The LLM should never see (or be tempted to query) unauthorized subs.
  - Returns name, id, and state. Minimal but sufficient for matching.
"""
from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from ..azure_clients import subscription_client
from ..config import ALLOWED_SUBSCRIPTIONS

log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def list_subscriptions() -> dict:
        """List all Azure subscriptions this server is allowed to query.

        Returns subscription name, ID, and state for each allowed subscription.
        Call this first when you need to find the right subscription ID for
        a cost or optimization query — match by name rather than guessing IDs.
        """
        client = subscription_client()
        subs = []
        for sub in client.subscriptions.list():
            if sub.subscription_id in ALLOWED_SUBSCRIPTIONS:
                subs.append({
                    "subscription_id": sub.subscription_id,
                    "name": sub.display_name,
                    "state": sub.state,
                })
        return {
            "count": len(subs),
            "subscriptions": subs,
        }

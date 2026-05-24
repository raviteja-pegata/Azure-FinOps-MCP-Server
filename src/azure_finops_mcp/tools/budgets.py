"""Budget status tools using Azure Consumption API.

Design decisions:
- Returns all budgets on a subscription with computed percent_consumed.
  The LLM can then highlight budgets near breach without us encoding
  threshold logic.
- Includes forecast_spend when Azure provides it (EA/MCA agreements).
"""
from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from ..azure_clients import consumption_client
from ..config import ALLOWED_SUBSCRIPTIONS, resolve_subscription

log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def get_budget_status(subscription_id: str | None = None) -> dict:
        """List all budgets on a subscription with current spend vs. limit.

        Returns budget name, limit amount, current spend, percent consumed,
        and time grain. Useful for answering "are any budgets about to breach"
        or "how much budget do we have left this month".

        Args:
            subscription_id: Subscription to query. Omit for default.
        """
        sub = resolve_subscription(subscription_id)
        scope = f"/subscriptions/{sub}"
        client = consumption_client(sub)

        budgets = []
        for b in client.budgets.list(scope=scope):
            current = b.current_spend.amount if b.current_spend else None
            forecast = (
                b.forecast_spend.amount
                if getattr(b, "forecast_spend", None)
                else None
            )
            limit = float(b.amount) if b.amount is not None else None
            pct = (current / limit * 100.0) if (current is not None and limit) else None

            budgets.append({
                "name": b.name,
                "amount_limit": limit,
                "current_spend": current,
                "forecast_spend": forecast,
                "percent_consumed": round(pct, 2) if pct is not None else None,
                "time_grain": b.time_grain,
                "category": b.category,
                "start_date": (
                    b.time_period.start_date.isoformat() if b.time_period else None
                ),
                "end_date": (
                    b.time_period.end_date.isoformat()
                    if (b.time_period and b.time_period.end_date)
                    else None
                ),
            })

        return {"scope": scope, "budget_count": len(budgets), "budgets": budgets}

    @mcp.tool()
    def get_portfolio_budget_status() -> dict:
        """Get budget status across ALL allowed subscriptions.

        Returns per-subscription budget summaries. Use this for portfolio-wide
        budget monitoring — quickly see which subscriptions have budgets at risk.
        """
        results = []
        errors = []

        for sub_id in sorted(ALLOWED_SUBSCRIPTIONS):
            try:
                status = get_budget_status(subscription_id=sub_id)
                results.append({
                    "subscription_id": sub_id,
                    "budget_count": status["budget_count"],
                    "budgets": status["budgets"],
                })
            except Exception as e:
                log.warning("Failed to get budgets for %s: %s", sub_id, e)
                errors.append({"subscription_id": sub_id, "error": str(e)})

        return {
            "subscription_count": len(results),
            "subscriptions": results,
            "errors": errors,
        }

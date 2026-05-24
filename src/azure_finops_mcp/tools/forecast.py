"""Forecast tool using Azure Cost Management forecast API.

Uses the same forecasting model that powers the Azure portal's cost
forecast view. Predicts daily spend from today through end of month
and returns a total.

Combine with get_month_to_date_cost + get_budget_status for a complete
"are we on track?" answer.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timezone

from azure.mgmt.costmanagement.models import (
    ForecastDataset,
    ForecastDefinition,
    ForecastTimePeriod,
    QueryAggregation,
)
from mcp.server.fastmcp import FastMCP

from ..azure_clients import cost_client
from ..config import ALLOWED_SUBSCRIPTIONS, resolve_subscription

log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def forecast_month_end_spend(subscription_id: str | None = None) -> dict:
        """Forecast total spend from today through the end of the current month.

        Uses Azure Cost Management's built-in forecast model. Returns daily
        forecast rows plus a total. Combine with get_month_to_date_cost to
        see actual-so-far + forecast-remaining, or compare against
        get_budget_status to check if you'll breach.

        Args:
            subscription_id: Subscription to forecast. Omit for default.
        """
        sub = resolve_subscription(subscription_id)
        scope = f"/subscriptions/{sub}"

        today = datetime.now(timezone.utc).date()
        last_day = calendar.monthrange(today.year, today.month)[1]
        end = date(today.year, today.month, last_day)
        start_dt = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
        end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)

        definition = ForecastDefinition(
            type="ActualCost",
            timeframe="Custom",
            time_period=ForecastTimePeriod(from_property=start_dt, to=end_dt),
            dataset=ForecastDataset(
                granularity="Daily",
                aggregation={
                    "totalCost": QueryAggregation(name="Cost", function="Sum"),
                },
            ),
            include_actual_cost=False,
            include_fresh_partial_cost=False,
        )

        result = cost_client().forecast.usage(scope=scope, parameters=definition)
        columns = [c.name for c in (result.columns or [])]
        rows = [dict(zip(columns, row)) for row in (result.rows or [])]

        # Sum up the forecast total
        cost_col = next((c for c in columns if c.lower().startswith("cost")), None)
        total_forecast = (
            sum((r.get(cost_col) or 0) for r in rows) if cost_col else None
        )

        return {
            "scope": scope,
            "forecast_period": {
                "start": today.isoformat(),
                "end": end.isoformat(),
            },
            "daily_forecast": rows,
            "total_forecast": round(total_forecast, 2) if total_forecast else None,
        }

    @mcp.tool()
    def forecast_portfolio_month_end_spend() -> dict:
        """Forecast month-end spend across ALL allowed subscriptions.

        Returns per-subscription forecasts and a portfolio total.
        Use for portfolio-level "will we be over budget this month" questions.
        """
        results = []
        grand_total = 0.0
        errors = []

        for sub_id in sorted(ALLOWED_SUBSCRIPTIONS):
            try:
                result = forecast_month_end_spend(subscription_id=sub_id)
                total = result.get("total_forecast") or 0.0
                results.append({
                    "subscription_id": sub_id,
                    "total_forecast": total,
                })
                grand_total += total
            except Exception as e:
                log.warning("Forecast failed for %s: %s", sub_id, e)
                errors.append({"subscription_id": sub_id, "error": str(e)})

        return {
            "forecast_period": results[0].get("forecast_period") if results else None,
            "subscription_count": len(results),
            "subscriptions": results,
            "grand_total_forecast": round(grand_total, 2),
            "errors": errors,
        }

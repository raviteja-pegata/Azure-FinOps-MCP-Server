"""Cost query tools using Azure Cost Management.

The Cost Management Query API is shaped like mini-OLAP: you POST a
QueryDefinition with timeframe, aggregation, grouping, and optional filters.
It returns columnar data (column names + rows).

Design decisions:
- Tools are narrow and purpose-built rather than one generic "query" tool.
  The LLM picks among 5 well-named tools far more reliably than it
  constructs a valid nested query object.
- Results are normalized to [{col: value, ...}, ...] dicts instead of the
  raw columnar format. The LLM narrates dicts naturally.
- get_portfolio_month_to_date_cost() loops over allowlisted subscriptions.
  It catches per-sub errors so one failing sub doesn't break the whole
  portfolio view. This is critical for 5+ sub environments where one sub
  might have different RBAC or be in a weird state.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from azure.mgmt.costmanagement.models import (
    QueryAggregation,
    QueryDataset,
    QueryDefinition,
    QueryGrouping,
    QueryTimePeriod,
    TimeframeType,
)
from mcp.server.fastmcp import FastMCP

from ..azure_clients import cost_client
from ..cache import cached
from ..config import ALLOWED_SUBSCRIPTIONS, resolve_subscription

log = logging.getLogger(__name__)

# Type aliases for tool parameter hints. The LLM sees these as enum-like
# choices in the tool schema, which helps it pick valid values.
Granularity = Literal["None", "Daily", "Monthly"]
GroupBy = Literal[
    "ServiceName",
    "ResourceGroup",
    "ResourceGroupName",
    "ResourceLocation",
    "ResourceId",
    "MeterCategory",
    "MeterSubCategory",
    "SubscriptionId",
    "SubscriptionName",
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_date(s: str, field: str) -> datetime:
    """Parse YYYY-MM-DD string to timezone-aware datetime."""
    try:
        d = date.fromisoformat(s)
    except ValueError as e:
        raise ValueError(f"{field} must be ISO format YYYY-MM-DD, got {s!r}") from e
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _run_query(scope: str, query: QueryDefinition) -> dict:
    """Execute a Cost Management query and normalize the result.

    Raw API returns columns + rows (columnar). We zip them into a list
    of dicts for easier LLM consumption.
    """
    client = cost_client()
    result = client.query.usage(scope=scope, parameters=query)
    columns = [c.name for c in (result.columns or [])]
    rows = [dict(zip(columns, row)) for row in (result.rows or [])]
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


def _sort_by_cost(result: dict, top_n: int) -> dict:
    """Sort rows by cost descending and truncate to top_n."""
    rows = result["rows"]
    cost_col = next((c for c in result["columns"] if c.lower().startswith("cost")), None)
    if cost_col:
        rows = sorted(rows, key=lambda r: r.get(cost_col) or 0, reverse=True)[:top_n]
    return {**result, "rows": rows, "row_count": len(rows)}


# ── Tool registration ────────────────────────────────────────────────────────

def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def get_cost_summary(
        start_date: str,
        end_date: str,
        subscription_id: str | None = None,
        granularity: Granularity = "None",
    ) -> dict:
        """Return total actual cost for a subscription between start_date and end_date.

        Use this for questions like "how much did we spend last month" or
        "what's our cost so far this quarter". For breakdowns by service or
        resource group, use get_cost_by_dimension instead.

        Args:
            start_date: Inclusive start date in YYYY-MM-DD format.
            end_date: Inclusive end date in YYYY-MM-DD format.
            subscription_id: Azure subscription to query. Omit to use the
                configured default.
            granularity: "None" for a single total, "Daily" or "Monthly"
                for a time series.
        """
        sub = resolve_subscription(subscription_id)
        start = _parse_date(start_date, "start_date")
        end = _parse_date(end_date, "end_date")
        if end < start:
            raise ValueError("end_date must be >= start_date")

        scope = f"/subscriptions/{sub}"
        query = QueryDefinition(
            type="ActualCost",
            timeframe=TimeframeType.CUSTOM,
            time_period=QueryTimePeriod(from_property=start, to=end),
            dataset=QueryDataset(
                granularity=granularity,
                aggregation={
                    "totalCost": QueryAggregation(name="Cost", function="Sum"),
                },
            ),
        )
        payload = {"scope": scope, "start": start_date, "end": end_date, "gran": granularity}
        result = cached("cost_summary", payload, lambda: _run_query(scope, query))
        return {**result, "scope": scope, "start_date": start_date, "end_date": end_date}

    @mcp.tool()
    def get_cost_by_dimension(
        start_date: str,
        end_date: str,
        group_by: GroupBy,
        subscription_id: str | None = None,
        top_n: int = 20,
    ) -> dict:
        """Break down cost by a dimension (service, resource group, location, etc.).

        Use this to answer "what service costs the most" or "which resource
        group is driving our bill". Results are sorted by cost descending.

        Args:
            start_date: Inclusive start date YYYY-MM-DD.
            end_date: Inclusive end date YYYY-MM-DD.
            group_by: Dimension to group by. Common choices:
                ServiceName (Azure service like VM, Storage),
                ResourceGroupName (resource group),
                ResourceLocation (region),
                MeterCategory / MeterSubCategory (billing meter),
                SubscriptionName (useful for cross-sub queries).
            subscription_id: Subscription to query. Omit for default.
            top_n: Max rows to return (default 20, sorted by cost desc).
        """
        sub = resolve_subscription(subscription_id)
        start = _parse_date(start_date, "start_date")
        end = _parse_date(end_date, "end_date")

        scope = f"/subscriptions/{sub}"
        query = QueryDefinition(
            type="ActualCost",
            timeframe=TimeframeType.CUSTOM,
            time_period=QueryTimePeriod(from_property=start, to=end),
            dataset=QueryDataset(
                granularity="None",
                aggregation={
                    "totalCost": QueryAggregation(name="Cost", function="Sum"),
                },
                grouping=[QueryGrouping(type="Dimension", name=group_by)],
            ),
        )
        payload = {"scope": scope, "start": start_date, "end": end_date, "group_by": group_by}
        result = cached("cost_by_dim", payload, lambda: _run_query(scope, query))
        return {**_sort_by_cost(result, top_n), "scope": scope}

    @mcp.tool()
    def get_cost_by_tag(
        start_date: str,
        end_date: str,
        tag_key: str,
        subscription_id: str | None = None,
        top_n: int = 20,
    ) -> dict:
        """Break down cost by values of a specific tag (e.g. env, team, costcenter).

        Essential for FinOps showback/chargeback. Untagged resources appear
        with an empty tag value — useful for finding tagging gaps.

        Args:
            start_date: YYYY-MM-DD.
            end_date: YYYY-MM-DD.
            tag_key: The tag name to group by (case-sensitive in Azure).
            subscription_id: Subscription to query.
            top_n: Max rows to return.
        """
        sub = resolve_subscription(subscription_id)
        start = _parse_date(start_date, "start_date")
        end = _parse_date(end_date, "end_date")

        scope = f"/subscriptions/{sub}"
        query = QueryDefinition(
            type="ActualCost",
            timeframe=TimeframeType.CUSTOM,
            time_period=QueryTimePeriod(from_property=start, to=end),
            dataset=QueryDataset(
                granularity="None",
                aggregation={
                    "totalCost": QueryAggregation(name="Cost", function="Sum"),
                },
                grouping=[QueryGrouping(type="TagKey", name=tag_key)],
            ),
        )
        payload = {"scope": scope, "start": start_date, "end": end_date, "tag": tag_key}
        result = cached("cost_by_tag", payload, lambda: _run_query(scope, query))
        return {**_sort_by_cost(result, top_n), "scope": scope, "tag_key": tag_key}

    @mcp.tool()
    def get_month_to_date_cost(subscription_id: str | None = None) -> dict:
        """Quick total spend from the 1st of the current month through today.

        Convenience tool for the common "how are we tracking this month"
        question. For custom date ranges, use get_cost_summary instead.

        Args:
            subscription_id: Subscription to query. Omit for default.
        """
        today = datetime.now(timezone.utc).date()
        first = today.replace(day=1)
        return get_cost_summary(
            start_date=first.isoformat(),
            end_date=today.isoformat(),
            subscription_id=subscription_id,
            granularity="None",
        )

    @mcp.tool()
    def get_portfolio_month_to_date_cost() -> dict:
        """Get month-to-date cost across ALL allowed subscriptions.

        Returns per-subscription totals plus a portfolio grand total.
        Use this when the user asks about total spend, portfolio overview,
        or cross-subscription comparisons.

        Errors on individual subscriptions are captured but don't block
        the rest — you'll see which subs succeeded and which failed.
        """
        today = datetime.now(timezone.utc).date()
        first = today.replace(day=1)

        results = []
        grand_total = 0.0
        errors = []

        for sub_id in sorted(ALLOWED_SUBSCRIPTIONS):
            try:
                result = get_cost_summary(
                    start_date=first.isoformat(),
                    end_date=today.isoformat(),
                    subscription_id=sub_id,
                    granularity="None",
                )
                # Extract the total cost from the first row
                cost_col = next(
                    (c for c in result.get("columns", []) if c.lower().startswith("cost")),
                    None,
                )
                total = 0.0
                for row in result.get("rows", []):
                    total += float(row.get(cost_col, 0)) if cost_col else 0.0
                results.append({
                    "subscription_id": sub_id,
                    "scope": result.get("scope"),
                    "total_cost": round(total, 2),
                })
                grand_total += total
            except Exception as e:
                log.warning("Failed to query subscription %s: %s", sub_id, e)
                errors.append({"subscription_id": sub_id, "error": str(e)})

        return {
            "period": {"start": first.isoformat(), "end": today.isoformat()},
            "subscription_count": len(results),
            "subscriptions": results,
            "grand_total": round(grand_total, 2),
            "errors": errors,
        }

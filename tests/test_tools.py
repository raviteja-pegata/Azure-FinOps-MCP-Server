"""
Unit tests for Azure FinOps MCP tools.

All Azure SDK calls are mocked — no real Azure credentials needed.

Run:
    pip install -e ".[dev]"
    pytest tests/ -v
"""

import pytest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# ── test helpers ──────────────────────────────────────────────────────────────

def capture_tools(register_fn):
    """Call register() with a mock FastMCP and return {fn_name: fn}."""
    tools = {}
    mock_mcp = MagicMock()
    mock_mcp.tool.return_value = lambda fn: tools.update({fn.__name__: fn}) or fn
    register_fn(mock_mcp)
    return tools


def cost_result(rows, columns=None):
    """Build a mock Azure Cost Management query result (columnar format)."""
    columns = columns or ["PreTaxCost", "Currency"]
    r = MagicMock()
    r.columns = [SimpleNamespace(name=c) for c in columns]
    r.rows = rows
    return r


def make_budget(name, limit, current, forecast=None, time_grain="Monthly",
                category="Cost", start=None, end=None):
    """Build a mock Azure budget object with nested spend/time fields."""
    b = MagicMock()
    b.name = name
    b.amount = limit
    b.current_spend = SimpleNamespace(amount=current)
    b.forecast_spend = SimpleNamespace(amount=forecast) if forecast is not None else None
    b.time_grain = time_grain
    b.category = category
    tp = MagicMock()
    tp.start_date = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    tp.end_date = end or datetime(2026, 12, 31, tzinfo=timezone.utc)
    b.time_period = tp
    return b


def metrics_response(data_points):
    """Build a mock Azure Monitor metrics query response."""
    timeseries = MagicMock()
    timeseries.data = data_points
    metric = MagicMock()
    metric.timeseries = [timeseries]
    response = MagicMock()
    response.metrics = [metric]
    return response


# ── config ────────────────────────────────────────────────────────────────────

class TestResolveSubscription:
    def test_returns_default_when_none_passed(self):
        with patch("azure_finops_mcp.config.ALLOWED_SUBSCRIPTIONS", frozenset(["sub-1"])), \
             patch("azure_finops_mcp.config.DEFAULT_SUBSCRIPTION", "sub-1"):
            from azure_finops_mcp.config import resolve_subscription
            assert resolve_subscription(None) == "sub-1"

    def test_returns_explicit_id_when_in_allowlist(self):
        with patch("azure_finops_mcp.config.ALLOWED_SUBSCRIPTIONS", frozenset(["sub-1", "sub-2"])), \
             patch("azure_finops_mcp.config.DEFAULT_SUBSCRIPTION", "sub-1"):
            from azure_finops_mcp.config import resolve_subscription
            assert resolve_subscription("sub-2") == "sub-2"

    def test_raises_for_subscription_not_in_allowlist(self):
        with patch("azure_finops_mcp.config.ALLOWED_SUBSCRIPTIONS", frozenset(["sub-1"])), \
             patch("azure_finops_mcp.config.DEFAULT_SUBSCRIPTION", "sub-1"):
            from azure_finops_mcp.config import resolve_subscription
            with pytest.raises(ValueError, match="AZURE_ALLOWED_SUBSCRIPTIONS"):
                resolve_subscription("sub-unauthorized")


# ── subscriptions ─────────────────────────────────────────────────────────────

class TestListSubscriptions:
    def _sub(self, sub_id, name, state="Enabled"):
        s = MagicMock()
        s.subscription_id = sub_id
        s.display_name = name
        s.state = state
        return s

    def test_filters_out_subscriptions_not_in_allowlist(self):
        from azure_finops_mcp.tools import subscriptions
        all_subs = [
            self._sub("sub-1", "Production"),
            self._sub("sub-2", "Dev"),
            self._sub("sub-99", "Not Allowed"),
        ]
        mock_client = MagicMock()
        mock_client.subscriptions.list.return_value = all_subs

        with patch("azure_finops_mcp.tools.subscriptions.subscription_client", return_value=mock_client), \
             patch("azure_finops_mcp.tools.subscriptions.ALLOWED_SUBSCRIPTIONS", frozenset(["sub-1", "sub-2"])):
            tools = capture_tools(subscriptions.register)
            result = tools["list_subscriptions"]()

        assert result["count"] == 2
        ids = {s["subscription_id"] for s in result["subscriptions"]}
        assert ids == {"sub-1", "sub-2"}
        assert "sub-99" not in ids

    def test_returns_empty_when_no_subs_match_allowlist(self):
        from azure_finops_mcp.tools import subscriptions
        mock_client = MagicMock()
        mock_client.subscriptions.list.return_value = [self._sub("sub-99", "Hidden")]

        with patch("azure_finops_mcp.tools.subscriptions.subscription_client", return_value=mock_client), \
             patch("azure_finops_mcp.tools.subscriptions.ALLOWED_SUBSCRIPTIONS", frozenset(["sub-1"])):
            tools = capture_tools(subscriptions.register)
            result = tools["list_subscriptions"]()

        assert result["count"] == 0
        assert result["subscriptions"] == []


# ── cost ──────────────────────────────────────────────────────────────────────

class TestCostTools:
    def test_get_cost_summary_returns_normalized_rows(self):
        from azure_finops_mcp.tools import cost
        mock_client = MagicMock()
        mock_client.query.usage.return_value = cost_result([[500.0, "USD"]])

        with patch("azure_finops_mcp.tools.cost.cost_client", return_value=mock_client), \
             patch("azure_finops_mcp.tools.cost.resolve_subscription", return_value="sub-1"), \
             patch("azure_finops_mcp.tools.cost.cached", side_effect=lambda ns, p, fn: fn()):
            tools = capture_tools(cost.register)
            result = tools["get_cost_summary"](start_date="2026-01-01", end_date="2026-01-31")

        assert result["row_count"] == 1
        assert result["rows"][0]["PreTaxCost"] == 500.0
        assert result["scope"] == "/subscriptions/sub-1"

    def test_get_cost_summary_raises_for_invalid_date(self):
        from azure_finops_mcp.tools import cost
        with patch("azure_finops_mcp.tools.cost.resolve_subscription", return_value="sub-1"):
            tools = capture_tools(cost.register)
            with pytest.raises(ValueError):
                tools["get_cost_summary"](start_date="not-a-date", end_date="2026-01-31")

    def test_get_cost_summary_raises_when_end_before_start(self):
        from azure_finops_mcp.tools import cost
        with patch("azure_finops_mcp.tools.cost.resolve_subscription", return_value="sub-1"):
            tools = capture_tools(cost.register)
            with pytest.raises(ValueError):
                tools["get_cost_summary"](start_date="2026-01-31", end_date="2026-01-01")

    def test_get_month_to_date_cost_scopes_to_subscription(self):
        from azure_finops_mcp.tools import cost
        mock_client = MagicMock()
        mock_client.query.usage.return_value = cost_result([[250.0, "USD"]])

        with patch("azure_finops_mcp.tools.cost.cost_client", return_value=mock_client), \
             patch("azure_finops_mcp.tools.cost.resolve_subscription", return_value="sub-1"), \
             patch("azure_finops_mcp.tools.cost.cached", side_effect=lambda ns, p, fn: fn()):
            tools = capture_tools(cost.register)
            result = tools["get_month_to_date_cost"]()

        assert result["scope"] == "/subscriptions/sub-1"
        assert result["row_count"] == 1

    def test_portfolio_cost_sums_grand_total(self):
        from azure_finops_mcp.tools import cost
        mock_client = MagicMock()
        mock_client.query.usage.side_effect = [
            cost_result([[300.0, "USD"]], ["Cost", "Currency"]),
            cost_result([[200.0, "USD"]], ["Cost", "Currency"]),
        ]

        with patch("azure_finops_mcp.tools.cost.cost_client", return_value=mock_client), \
             patch("azure_finops_mcp.tools.cost.resolve_subscription", side_effect=lambda x: x), \
             patch("azure_finops_mcp.tools.cost.ALLOWED_SUBSCRIPTIONS", frozenset(["sub-1", "sub-2"])):
            tools = capture_tools(cost.register)
            result = tools["get_portfolio_month_to_date_cost"]()

        assert result["subscription_count"] == 2
        assert result["grand_total"] == pytest.approx(500.0)
        assert result["errors"] == []

    def test_portfolio_cost_isolates_per_sub_errors(self):
        from azure_finops_mcp.tools import cost
        mock_client = MagicMock()
        mock_client.query.usage.side_effect = [
            cost_result([[300.0, "USD"]], ["Cost", "Currency"]),
            Exception("Subscription unavailable"),
        ]

        with patch("azure_finops_mcp.tools.cost.cost_client", return_value=mock_client), \
             patch("azure_finops_mcp.tools.cost.resolve_subscription", side_effect=lambda x: x), \
             patch("azure_finops_mcp.tools.cost.cached", side_effect=lambda ns, p, fn: fn()), \
             patch("azure_finops_mcp.tools.cost.ALLOWED_SUBSCRIPTIONS", frozenset(["sub-1", "sub-2"])):
            tools = capture_tools(cost.register)
            result = tools["get_portfolio_month_to_date_cost"]()

        assert result["grand_total"] == pytest.approx(300.0)
        assert len(result["errors"]) == 1
        assert "Subscription unavailable" in result["errors"][0]["error"]


# ── budgets ───────────────────────────────────────────────────────────────────

class TestBudgets:
    def test_budget_status_calculates_percent_consumed(self):
        from azure_finops_mcp.tools import budgets
        mock_client = MagicMock()
        mock_client.budgets.list.return_value = [
            make_budget("monthly", limit=10000.0, current=7500.0, forecast=9000.0)
        ]

        with patch("azure_finops_mcp.tools.budgets.consumption_client", return_value=mock_client), \
             patch("azure_finops_mcp.tools.budgets.resolve_subscription", return_value="sub-1"):
            tools = capture_tools(budgets.register)
            result = tools["get_budget_status"]()

        assert result["budget_count"] == 1
        b = result["budgets"][0]
        assert b["name"] == "monthly"
        assert b["amount_limit"] == 10000.0
        assert b["current_spend"] == 7500.0
        assert b["percent_consumed"] == 75.0

    def test_budget_status_returns_empty_when_no_budgets(self):
        from azure_finops_mcp.tools import budgets
        mock_client = MagicMock()
        mock_client.budgets.list.return_value = []

        with patch("azure_finops_mcp.tools.budgets.consumption_client", return_value=mock_client), \
             patch("azure_finops_mcp.tools.budgets.resolve_subscription", return_value="sub-1"):
            tools = capture_tools(budgets.register)
            result = tools["get_budget_status"]()

        assert result["budget_count"] == 0
        assert result["budgets"] == []

    def test_portfolio_budget_isolates_per_sub_errors(self):
        from azure_finops_mcp.tools import budgets
        mock_client = MagicMock()
        mock_client.budgets.list.side_effect = Exception("Permission denied")

        with patch("azure_finops_mcp.tools.budgets.consumption_client", return_value=mock_client), \
             patch("azure_finops_mcp.tools.budgets.resolve_subscription", side_effect=lambda x: x), \
             patch("azure_finops_mcp.tools.budgets.ALLOWED_SUBSCRIPTIONS", frozenset(["sub-1", "sub-2"])):
            tools = capture_tools(budgets.register)
            result = tools["get_portfolio_budget_status"]()

        assert len(result["errors"]) == 2
        assert "Permission denied" in result["errors"][0]["error"]


# ── forecast ──────────────────────────────────────────────────────────────────

class TestForecast:
    def test_forecast_month_end_returns_total_and_period(self):
        from azure_finops_mcp.tools import forecast
        mock_client = MagicMock()
        mock_client.forecast.usage.return_value = cost_result([[1200.0, "USD"]], ["Cost", "Currency"])

        with patch("azure_finops_mcp.tools.forecast.cost_client", return_value=mock_client), \
             patch("azure_finops_mcp.tools.forecast.resolve_subscription", return_value="sub-1"):
            tools = capture_tools(forecast.register)
            result = tools["forecast_month_end_spend"]()

        assert result["scope"] == "/subscriptions/sub-1"
        assert result["total_forecast"] == pytest.approx(1200.0)
        assert "forecast_period" in result

    def test_forecast_portfolio_sums_grand_total(self):
        from azure_finops_mcp.tools import forecast
        mock_client = MagicMock()
        mock_client.forecast.usage.side_effect = [
            cost_result([[500.0, "USD"]], ["Cost", "Currency"]),
            cost_result([[700.0, "USD"]], ["Cost", "Currency"]),
        ]

        with patch("azure_finops_mcp.tools.forecast.cost_client", return_value=mock_client), \
             patch("azure_finops_mcp.tools.forecast.resolve_subscription", side_effect=lambda x: x), \
             patch("azure_finops_mcp.tools.forecast.ALLOWED_SUBSCRIPTIONS", frozenset(["sub-1", "sub-2"])):
            tools = capture_tools(forecast.register)
            result = tools["forecast_portfolio_month_end_spend"]()

        assert result["grand_total_forecast"] == pytest.approx(1200.0)
        assert result["errors"] == []

    def test_forecast_portfolio_isolates_per_sub_errors(self):
        from azure_finops_mcp.tools import forecast
        mock_client = MagicMock()
        mock_client.forecast.usage.side_effect = [
            cost_result([[500.0, "USD"]], ["Cost", "Currency"]),
            Exception("Forecast API unavailable"),
        ]

        with patch("azure_finops_mcp.tools.forecast.cost_client", return_value=mock_client), \
             patch("azure_finops_mcp.tools.forecast.resolve_subscription", side_effect=lambda x: x), \
             patch("azure_finops_mcp.tools.forecast.ALLOWED_SUBSCRIPTIONS", frozenset(["sub-1", "sub-2"])):
            tools = capture_tools(forecast.register)
            result = tools["forecast_portfolio_month_end_spend"]()

        assert result["grand_total_forecast"] == pytest.approx(500.0)
        assert len(result["errors"]) == 1
        assert "Forecast API unavailable" in result["errors"][0]["error"]


# ── optimization ──────────────────────────────────────────────────────────────

class TestOptimization:
    def _advisor_rec(self, problem, savings=5000.0, currency="USD", impact="High"):
        rec = MagicMock()
        rec.category = "Cost"
        rec.impact = impact
        rec.short_description = SimpleNamespace(problem=problem)
        rec.resource_metadata = SimpleNamespace(
            resource_id="/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1"
        )
        rec.extended_properties = {
            "annualSavingsAmount": str(savings),
            "savingsCurrency": currency,
        }
        return rec

    def test_find_idle_resources_returns_unattached_disks(self):
        from azure_finops_mcp.tools import optimization
        mock_graph = MagicMock()
        mock_graph.resources.return_value = MagicMock(data=[
            {"id": "/sub/disk1", "name": "disk1", "resourceGroup": "rg1",
             "location": "eastus", "sizeGb": 128, "sku": "Premium_LRS"}
        ])

        with patch("azure_finops_mcp.tools.optimization.resource_graph_client", return_value=mock_graph), \
             patch("azure_finops_mcp.tools.optimization.resolve_subscription", return_value="sub-1"), \
             patch("azure_finops_mcp.tools.optimization.ALLOWED_SUBSCRIPTIONS", frozenset(["sub-1"])):
            tools = capture_tools(optimization.register)
            result = tools["find_idle_resources"]()

        assert result["subscription_id"] == "sub-1"
        assert len(result["findings"]["unattached_disks"]) == 1
        assert result["findings"]["unattached_disks"][0]["name"] == "disk1"

    def test_advisor_recommendations_sorted_by_savings_descending(self):
        from azure_finops_mcp.tools import optimization
        mock_client = MagicMock()
        mock_client.recommendations.list.return_value = [
            self._advisor_rec("Resize VM", savings=1000.0),
            self._advisor_rec("Delete unused disk", savings=5000.0),
            self._advisor_rec("Reserved instance", savings=2500.0),
        ]

        with patch("azure_finops_mcp.tools.optimization.advisor_client", return_value=mock_client), \
             patch("azure_finops_mcp.tools.optimization.resolve_subscription", return_value="sub-1"):
            tools = capture_tools(optimization.register)
            result = tools["get_advisor_recommendations"]()

        assert result["count"] == 3
        savings_list = [r["annual_savings"] for r in result["recommendations"]]
        assert savings_list == sorted(savings_list, reverse=True)

    def test_advisor_filters_out_non_cost_categories(self):
        from azure_finops_mcp.tools import optimization
        security_rec = MagicMock()
        security_rec.category = "Security"
        mock_client = MagicMock()
        mock_client.recommendations.list.return_value = [security_rec]

        with patch("azure_finops_mcp.tools.optimization.advisor_client", return_value=mock_client), \
             patch("azure_finops_mcp.tools.optimization.resolve_subscription", return_value="sub-1"):
            tools = capture_tools(optimization.register)
            result = tools["get_advisor_recommendations"]()

        assert result["count"] == 0
        assert result["recommendations"] == []

    def test_vm_utilization_computes_mean_and_peak_cpu(self):
        from azure_finops_mcp.tools import optimization
        data = [
            SimpleNamespace(timestamp=datetime(2026, 5, 1, tzinfo=timezone.utc), average=20.0, maximum=45.0),
            SimpleNamespace(timestamp=datetime(2026, 5, 2, tzinfo=timezone.utc), average=40.0, maximum=80.0),
            SimpleNamespace(timestamp=datetime(2026, 5, 3, tzinfo=timezone.utc), average=30.0, maximum=60.0),
        ]
        mock_client = MagicMock()
        mock_client.query_resource.return_value = metrics_response(data)

        vm_id = "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1"
        with patch("azure_finops_mcp.tools.optimization.metrics_client", return_value=mock_client):
            tools = capture_tools(optimization.register)
            result = tools["get_vm_utilization"](vm_resource_id=vm_id)

        assert result["summary"]["mean_cpu_pct"] == round((20.0 + 40.0 + 30.0) / 3, 2)
        assert result["summary"]["peak_cpu_pct"] == 80.0
        assert result["summary"]["sample_count"] == 3
        assert len(result["hourly_points"]) == 3

    def test_vm_utilization_returns_none_stats_when_no_data(self):
        from azure_finops_mcp.tools import optimization
        mock_client = MagicMock()
        mock_client.query_resource.return_value = metrics_response([])

        vm_id = "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1"
        with patch("azure_finops_mcp.tools.optimization.metrics_client", return_value=mock_client):
            tools = capture_tools(optimization.register)
            result = tools["get_vm_utilization"](vm_resource_id=vm_id)

        assert result["summary"]["mean_cpu_pct"] is None
        assert result["summary"]["peak_cpu_pct"] is None
        assert result["summary"]["sample_count"] == 0

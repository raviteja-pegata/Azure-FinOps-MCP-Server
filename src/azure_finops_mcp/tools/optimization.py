"""Optimization tools: idle resources, Advisor recs, rightsizing.

Design decisions:
- Resource Graph (KQL) for idle detection, not per-resource SDK calls.
  One KQL query across thousands of resources in milliseconds vs O(n)
  API calls that would get you throttled on any real subscription.
- Advisor for cost recommendations — they're pre-computed daily by Azure
  at no cost. Sorted by annual savings so the highest-value items appear first.
- VM utilization via Azure Monitor for rightsizing validation. Advisor says
  "downsize this VM" but you want to confirm CPU is actually low before acting.
  This tool provides that confirmation.
- find_idle_resources_portfolio scans ALL allowed subs. Each sub's errors
  are captured independently so one broken sub doesn't hide waste in others.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from azure.mgmt.resourcegraph.models import QueryRequest
from azure.monitor.query import MetricAggregationType
from mcp.server.fastmcp import FastMCP

from ..azure_clients import advisor_client, metrics_client, resource_graph_client
from ..config import ALLOWED_SUBSCRIPTIONS, resolve_subscription

log = logging.getLogger(__name__)


# ── Resource Graph KQL queries ───────────────────────────────────────────────
# Each query finds a specific category of likely-wasted resources.
# These are deliberately simple and high-confidence signals.

_IDLE_QUERIES = {
    "unattached_disks": """
        Resources
        | where type =~ 'microsoft.compute/disks'
        | where properties.diskState == 'Unattached'
        | project id, name, resourceGroup, location,
                  sizeGb = properties.diskSizeGB,
                  sku = sku.name
    """,
    "unassociated_public_ips": """
        Resources
        | where type =~ 'microsoft.network/publicipaddresses'
        | where isnull(properties.ipConfiguration)
        | project id, name, resourceGroup, location,
                  sku = sku.name,
                  allocation = properties.publicIPAllocationMethod
    """,
    "unattached_nics": """
        Resources
        | where type =~ 'microsoft.network/networkinterfaces'
        | where isnull(properties.virtualMachine)
        | project id, name, resourceGroup, location
    """,
    "stopped_not_deallocated_vms": """
        Resources
        | where type =~ 'microsoft.compute/virtualmachines'
        | extend powerState = tostring(
              properties.extended.instanceView.powerState.displayStatus)
        | where powerState == 'VM stopped'
        | project id, name, resourceGroup, location, powerState,
                  vmSize = properties.hardwareProfile.vmSize
    """,
}


def _run_graph_query(kql: str, subscription_ids: list[str]) -> list[dict]:
    """Execute a Resource Graph query against one or more subscriptions."""
    req = QueryRequest(query=kql, subscriptions=subscription_ids)
    client = resource_graph_client()
    result = client.resources(req)
    return list(result.data or [])


# ── Tool registration ────────────────────────────────────────────────────────

def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def find_idle_resources(subscription_id: str | None = None) -> dict:
        """Find likely-wasted resources in a single subscription.

        Scans for: unattached managed disks, unassociated public IPs,
        unattached network interfaces, and VMs that are stopped but not
        deallocated (still incurring compute charges).

        Uses Azure Resource Graph for fast, single-query scanning.
        Each finding includes resource ID, name, resource group, and
        relevant metadata.

        Args:
            subscription_id: Subscription to scan. Omit for default.
        """
        sub = resolve_subscription(subscription_id)
        findings = {}
        for name, kql in _IDLE_QUERIES.items():
            try:
                findings[name] = _run_graph_query(kql, [sub])
            except Exception as e:
                log.warning("Resource Graph query '%s' failed: %s", name, e)
                findings[name] = {"error": str(e)}

        total = sum(len(v) for v in findings.values() if isinstance(v, list))
        return {
            "subscription_id": sub,
            "total_findings": total,
            "findings": findings,
            "note": (
                "These are candidates — validate before deleting. A disk may be "
                "a snapshot source, an IP may be reserved for DR."
            ),
        }

    @mcp.tool()
    def find_idle_resources_portfolio() -> dict:
        """Find idle resources across ALL allowed subscriptions.

        Aggregates unattached disks, stranded IPs/NICs, and stopped VMs
        across your entire portfolio. Use this for org-wide waste hunting.
        """
        all_findings = {}
        errors = []
        grand_total = 0

        for sub_id in sorted(ALLOWED_SUBSCRIPTIONS):
            try:
                result = find_idle_resources(subscription_id=sub_id)
                all_findings[sub_id] = result["findings"]
                grand_total += result["total_findings"]
            except Exception as e:
                log.warning("Idle scan failed for %s: %s", sub_id, e)
                errors.append({"subscription_id": sub_id, "error": str(e)})

        return {
            "subscription_count": len(all_findings),
            "grand_total_findings": grand_total,
            "subscriptions": all_findings,
            "errors": errors,
        }

    @mcp.tool()
    def get_advisor_recommendations(
        subscription_id: str | None = None,
        max_results: int = 50,
    ) -> dict:
        """Get Azure Advisor cost recommendations for a subscription.

        Advisor pre-computes rightsizing, reserved-instance, and shutdown
        recommendations with projected annual savings. Results are sorted by
        annual savings descending — highest-value items first.

        This is often the single highest-leverage tool: Advisor does the
        analysis; you just need to review and act.

        Args:
            subscription_id: Subscription to query. Omit for default.
            max_results: Cap the number of recommendations returned (default 50).
        """
        sub = resolve_subscription(subscription_id)
        client = advisor_client(sub)

        recs = []
        for r in client.recommendations.list():
            if (r.category or "").lower() != "cost":
                continue

            short = r.short_description.problem if r.short_description else None
            ext = getattr(r, "extended_properties", None) or {}
            annual_savings = ext.get("annualSavingsAmount") or ext.get("savingsAmount")

            recs.append({
                "id": r.id,
                "impact": r.impact,
                "problem": short,
                "resource_id": (
                    r.resource_metadata.resource_id if r.resource_metadata else None
                ),
                "annual_savings": annual_savings,
                "savings_currency": ext.get("savingsCurrency"),
                "extended_properties": ext,
            })
            if len(recs) >= max_results:
                break

        # Sort by annual savings descending
        def _savings(rec: dict) -> float:
            try:
                return float(rec.get("annual_savings") or 0)
            except (TypeError, ValueError):
                return 0.0

        recs.sort(key=_savings, reverse=True)
        return {"subscription_id": sub, "count": len(recs), "recommendations": recs}

    @mcp.tool()
    def get_vm_utilization(
        vm_resource_id: str,
        lookback_days: int = 14,
    ) -> dict:
        """Get CPU utilization stats for a specific VM.

        Use this to validate rightsizing recommendations — if Advisor says
        "downsize this VM", call this to confirm CPU is actually low before
        acting. Returns hourly CPU data plus a summary (mean, peak, sample count).

        Args:
            vm_resource_id: Full ARM resource ID, like
                /subscriptions/.../resourceGroups/.../providers/
                Microsoft.Compute/virtualMachines/my-vm
            lookback_days: How many days back to query (default 14).
        """
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)
        client = metrics_client()

        response = client.query_resource(
            resource_uri=vm_resource_id,
            metric_names=["Percentage CPU"],
            timespan=(start, end),
            granularity=timedelta(hours=1),
            aggregations=[
                MetricAggregationType.AVERAGE,
                MetricAggregationType.MAXIMUM,
            ],
        )

        points = []
        for metric in response.metrics:
            for ts in metric.timeseries:
                for dp in ts.data:
                    points.append({
                        "timestamp": dp.timestamp.isoformat() if dp.timestamp else None,
                        "avg_cpu_pct": dp.average,
                        "max_cpu_pct": dp.maximum,
                    })

        avgs = [p["avg_cpu_pct"] for p in points if p["avg_cpu_pct"] is not None]
        maxes = [p["max_cpu_pct"] for p in points if p["max_cpu_pct"] is not None]

        return {
            "vm_resource_id": vm_resource_id,
            "lookback_days": lookback_days,
            "summary": {
                "mean_cpu_pct": round(sum(avgs) / len(avgs), 2) if avgs else None,
                "peak_cpu_pct": round(max(maxes), 2) if maxes else None,
                "sample_count": len(points),
            },
            "hourly_points": points,
        }

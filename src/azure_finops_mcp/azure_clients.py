"""Shared Azure SDK clients.

Design decisions:
- One DefaultAzureCredential instance shared across all clients. It's
  thread-safe and handles token refresh internally.
- @lru_cache means each client is created once per subscription and reused.
  No redundant client objects, no redundant auth handshakes.
- Clients that aren't subscription-scoped (CostManagement, ResourceGraph,
  Metrics) get maxsize=1. Subscription-scoped clients get maxsize=8 to
  cover your allowlist without eviction churn.
- exclude_interactive_browser_credential=True prevents the server from
  hanging waiting for a browser window when running as a headless subprocess
  under Claude Desktop. Use `az login` instead.
"""
from __future__ import annotations

from functools import lru_cache

from azure.identity import DefaultAzureCredential
from azure.mgmt.advisor import AdvisorManagementClient
from azure.mgmt.consumption import ConsumptionManagementClient
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.subscription import SubscriptionClient
from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.monitor.query import MetricsQueryClient


@lru_cache(maxsize=1)
def credential() -> DefaultAzureCredential:
    """Single credential instance for the process lifetime."""
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


# ── Clients NOT scoped to a subscription ─────────────────────────────────────

@lru_cache(maxsize=1)
def cost_client() -> CostManagementClient:
    """Cost Management takes scope per-call, not in constructor."""
    return CostManagementClient(credential=credential())


@lru_cache(maxsize=1)
def resource_graph_client() -> ResourceGraphClient:
    return ResourceGraphClient(credential=credential())


@lru_cache(maxsize=1)
def metrics_client() -> MetricsQueryClient:
    return MetricsQueryClient(credential=credential())


@lru_cache(maxsize=1)
def subscription_client() -> SubscriptionClient:
    return SubscriptionClient(credential=credential())


# ── Clients scoped to a subscription ─────────────────────────────────────────

@lru_cache(maxsize=8)
def consumption_client(subscription_id: str) -> ConsumptionManagementClient:
    return ConsumptionManagementClient(
        credential=credential(), subscription_id=subscription_id
    )


@lru_cache(maxsize=8)
def advisor_client(subscription_id: str) -> AdvisorManagementClient:
    return AdvisorManagementClient(
        credential=credential(), subscription_id=subscription_id
    )


@lru_cache(maxsize=8)
def resource_client(subscription_id: str) -> ResourceManagementClient:
    return ResourceManagementClient(
        credential=credential(), subscription_id=subscription_id
    )


@lru_cache(maxsize=8)
def compute_client(subscription_id: str) -> ComputeManagementClient:
    return ComputeManagementClient(
        credential=credential(), subscription_id=subscription_id
    )

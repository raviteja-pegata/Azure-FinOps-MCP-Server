"""Azure FinOps MCP Server.

This is the entry point. It creates the FastMCP instance, registers all
tool modules, and starts the JSON-RPC event loop.

Architecture recap:
- One FastMCP instance = one server process
- Each tool module exposes register(mcp) which decorates functions with @mcp.tool()
- mcp.run() starts the stdio event loop (blocks forever, reads from stdin,
  writes to stdout, dispatches tool calls to registered functions)
- For remote deployment, swap mcp.run() for mcp.run(transport="streamable-http")
"""
from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP

from .config import LOG_LEVEL
from .tools import budgets, cost, forecast, optimization, subscriptions

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("azure-finops-mcp")

# When running as HTTP server, accept requests from any host (Container Apps proxy)
_host = os.environ.get("MCP_HOST", "0.0.0.0") \
    if os.environ.get("MCP_TRANSPORT") == "streamable-http" else "127.0.0.1"
_port = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP(
    "azure-finops",
    host=_host,
    port=_port,
    instructions=(
        "Azure FinOps server for cost analysis, budgets, forecasts, and "
        "resource optimization across multiple subscriptions.\n\n"
        "Typical workflow:\n"
        "1. Call list_subscriptions to discover available subscriptions and "
        "   match by name to the user's request.\n"
        "2. Use get_cost_summary or get_cost_by_dimension for per-subscription "
        "   cost queries. Use get_portfolio_month_to_date_cost for totals "
        "   across all subscriptions.\n"
        "3. Use get_budget_status to check budget consumption.\n"
        "4. Use find_idle_resources or get_advisor_recommendations for "
        "   optimization opportunities.\n"
        "5. Use get_vm_utilization to validate rightsizing recommendations.\n\n"
        "All costs are returned in the billing currency. Subscription IDs "
        "can be omitted — the configured default will be used."
    ),
)

# Register all tool modules
subscriptions.register(mcp)
cost.register(mcp)
budgets.register(mcp)
optimization.register(mcp)
forecast.register(mcp)


def main() -> None:
    """Start the MCP server.

    Transport is controlled by the MCP_TRANSPORT env var:
      - "streamable-http"  → HTTP server (Azure Container Apps / remote)
      - anything else      → stdio (default, local Claude Desktop / Cursor)

    HTTP host/port are controlled by MCP_HOST (default 0.0.0.0) and
    MCP_PORT (default 8000).
    """
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        log.info("Starting Azure FinOps MCP server (HTTP %s:%s)", _host, _port)
        mcp.run(transport="streamable-http")
    else:
        log.info("Starting Azure FinOps MCP server (stdio)")
        mcp.run()


if __name__ == "__main__":
    main()

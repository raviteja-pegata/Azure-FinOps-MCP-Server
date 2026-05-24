# Azure FinOps MCP Server

<!-- mcp-name: io.github.raviteja-pegata/azure-finops-mcp -->

An MCP server that gives LLM clients (Claude Desktop, Claude Code, VS Code, Cursor)
conversational access to Azure cost analysis, budget tracking, forecasting, and resource
optimization — across multiple subscriptions.

## Tools

### Discovery
| Tool | Purpose |
|---|---|
| `list_subscriptions` | List allowed subscriptions with friendly names |

### Cost Analysis
| Tool | Purpose |
|---|---|
| `get_cost_summary` | Total cost for a date range (single sub) |
| `get_cost_by_dimension` | Cost breakdown by service / RG / location / meter |
| `get_cost_by_tag` | Cost grouped by tag value (showback/chargeback) |
| `get_month_to_date_cost` | Current-month spend (single sub) |
| `get_portfolio_month_to_date_cost` | Current-month spend across ALL subs |

### Budgets
| Tool | Purpose |
|---|---|
| `get_budget_status` | Budget consumption for a single sub |
| `get_portfolio_budget_status` | Budget status across ALL subs |

### Optimization
| Tool | Purpose |
|---|---|
| `find_idle_resources` | Unattached disks, stranded IPs/NICs, stopped VMs |
| `find_idle_resources_portfolio` | Idle resources across ALL subs |
| `get_advisor_recommendations` | Azure Advisor cost recs with annual savings |
| `get_vm_utilization` | CPU stats to validate rightsizing |

### Forecasting
| Tool | Purpose |
|---|---|
| `forecast_month_end_spend` | Predicted month-end cost (single sub) |
| `forecast_portfolio_month_end_spend` | Predicted month-end cost across ALL subs |

## Prerequisites

- Python 3.11+
- Azure CLI installed and logged in (`az login`)

## Azure RBAC Permissions

The identity running this server (your user, a service principal, or a managed identity)
needs three roles assigned on **each subscription** you want to query:

| Role | Purpose |
|---|---|
| **Cost Management Reader** | Cost analysis, forecasting, budget queries |
| **Reader** | Resource inventory via Resource Graph |
| **Monitoring Reader** | VM utilization metrics via Azure Monitor |

### Assign via Azure CLI

```bash
SUBSCRIPTION_ID="<your-subscription-id>"
PRINCIPAL_ID="<object-id-of-user-sp-or-managed-identity>"

for ROLE in "Cost Management Reader" "Reader" "Monitoring Reader"; do
  az role assignment create \
    --assignee "$PRINCIPAL_ID" \
    --role "$ROLE" \
    --scope "/subscriptions/$SUBSCRIPTION_ID"
done
```

Repeat for each subscription listed in `AZURE_ALLOWED_SUBSCRIPTIONS`.

### Local development (your own user)

```bash
az login
az account set --subscription "<your-subscription-id>"

# Check your object ID
az ad signed-in-user show --query id -o tsv
```

Your user already has these roles if you're a subscription Owner or Contributor.
If not, ask your Azure admin to assign them.

### Managed Identity (Container Apps deployment)

After deploying with `deploy.sh`, the script automatically assigns these three roles
to the Container App's system-assigned managed identity on each allowed subscription.
No credentials or secrets are needed — `DefaultAzureCredential` picks up the
managed identity automatically at runtime.

## Install

```bash
git clone <your-repo-url> azure-finops-mcp
cd azure-finops-mcp

python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .

cp .env.example .env
# Edit .env: set your subscription IDs
```

## Configure .env

```bash
# Required: comma-separated subscription IDs the server may query
AZURE_ALLOWED_SUBSCRIPTIONS=sub-id-1,sub-id-2,sub-id-3

# Required: default subscription (must be in the list above)
AZURE_DEFAULT_SUBSCRIPTION=sub-id-1
```

## Test with MCP Inspector

The Inspector is a web UI that lets you call tools interactively and see
raw JSON-RPC messages. Always test here before connecting to Claude Desktop.

```bash
# Use the venv's python3 explicitly — the Inspector launches a subprocess
# and needs the binary that has mcp + azure SDKs installed.
npx @modelcontextprotocol/inspector $(which python3) -m azure_finops_mcp.server
```

In the Inspector UI:
1. Verify Transport Type is **STDIO**
2. Click **Connect** — should succeed and show "azure-finops" as the server name
3. Navigate to Tools, click List Tools — you should see all 15 tools
4. Try `list_subscriptions` first (no arguments needed)
5. Try `get_month_to_date_cost` (no arguments needed — uses default sub)

## Client Configuration

Find the absolute path to your venv's Python first — you'll need it in every config below:

```bash
# With your venv activated:
which python3
# e.g. /Users/yourname/azure-finops-mcp/.venv/bin/python3
```

---

### Claude Desktop

Edit `claude_desktop_config.json`:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "azure-finops": {
      "command": "/absolute/path/to/.venv/bin/python3",
      "args": ["-m", "azure_finops_mcp.server"],
      "env": {
        "AZURE_ALLOWED_SUBSCRIPTIONS": "sub-1,sub-2,sub-3",
        "AZURE_DEFAULT_SUBSCRIPTION": "sub-1",
        "FINOPS_CACHE_TTL_SECONDS": "900"
      }
    }
  }
}
```

Restart Claude Desktop. A tool icon in the chat input confirms the server connected.

---

### VS Code (GitHub Copilot / Agent mode)

Create `.vscode/mcp.json` in your workspace (or add to user `settings.json` under `"mcp"`):

```json
{
  "servers": {
    "azure-finops": {
      "type": "stdio",
      "command": "/absolute/path/to/.venv/bin/python3",
      "args": ["-m", "azure_finops_mcp.server"],
      "env": {
        "AZURE_ALLOWED_SUBSCRIPTIONS": "sub-1,sub-2,sub-3",
        "AZURE_DEFAULT_SUBSCRIPTION": "sub-1",
        "FINOPS_CACHE_TTL_SECONDS": "900"
      }
    }
  }
}
```

Requires VS Code 1.99+ with the **GitHub Copilot** extension. Open the Chat panel,
switch to **Agent** mode, and the `azure-finops` tools will appear automatically.

---

### Cursor

Create or edit `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "azure-finops": {
      "command": "/absolute/path/to/.venv/bin/python3",
      "args": ["-m", "azure_finops_mcp.server"],
      "env": {
        "AZURE_ALLOWED_SUBSCRIPTIONS": "sub-1,sub-2,sub-3",
        "AZURE_DEFAULT_SUBSCRIPTION": "sub-1",
        "FINOPS_CACHE_TTL_SECONDS": "900"
      }
    }
  }
}
```

Or add it via **Cursor Settings → MCP → Add new global MCP server**.
Restart Cursor. The tools appear in Cursor's Agent/Composer panel.

---

### Claude Code (CLI)

```bash
claude mcp add azure-finops \
  /absolute/path/to/.venv/bin/python3 \
  -m azure_finops_mcp.server \
  -e AZURE_ALLOWED_SUBSCRIPTIONS=sub-1,sub-2,sub-3 \
  -e AZURE_DEFAULT_SUBSCRIPTION=sub-1
```

---

### Remote HTTP (after deploying to Azure Container Apps)

All clients support connecting to the deployed server over HTTP — no local Python needed:

**Claude Desktop / Cursor** — add to the same config files above:
```json
{
  "mcpServers": {
    "azure-finops": {
      "type": "http",
      "url": "https://<your-container-app-fqdn>/mcp"
    }
  }
}
```

**VS Code** — in `.vscode/mcp.json`:
```json
{
  "servers": {
    "azure-finops": {
      "type": "http",
      "url": "https://<your-container-app-fqdn>/mcp"
    }
  }
}
```

**Claude Web** — Settings → Integrations → Add → `https://<your-container-app-fqdn>/mcp`

## Example Prompts

Try these once connected:

- "What are our allowed subscriptions?"
- "What's our total month-to-date spend across all subscriptions?"
- "Which 10 services cost the most on our prod subscription last month?"
- "Break down last quarter's spend by the `costcenter` tag."
- "Are any budgets close to breaching?"
- "Show me idle resources across all our subscriptions."
- "What does Azure Advisor recommend for cost savings?"
- "Is VM `my-analytics-vm` actually being used? Check its CPU over 14 days."
- "Compare our forecast for this month against our budgets."

## Architecture

```
Claude Desktop ◄─┐
VS Code        ◄─┤
Cursor         ◄─┼──stdio / HTTP──► Azure FinOps MCP Server ◄──REST──► Azure APIs
Claude Code    ◄─┤                        │
Claude Web     ◄─┘                        ├── config.py          ← env + allowlist
                                          ├── azure_clients.py   ← shared credential
                                          ├── cache.py           ← TTL cache
                                          ├── server.py          ← FastMCP + registration
                                          └── tools/
                                              ├── subscriptions  ← discovery
                                              ├── cost           ← queries + portfolio
                                              ├── budgets        ← budget status
                                              ├── optimization   ← idle + advisor + metrics
                                              └── forecast       ← predictions
```

### Key design decisions

**Narrow tools over flexible tools.** The LLM picks among well-named tools
far better than it constructs complex query objects. 15 purpose-built tools
beats 3 configurable ones.

**Subscription allowlist.** A frozenset loaded from env. Every tool calls
`resolve_subscription()` which refuses any ID not in the list. Prevents the
LLM from querying unauthorized subscriptions — important for prompt injection
defense.

**Portfolio tools catch per-sub errors.** When querying 5+ subscriptions, one
might have different RBAC or be in a weird state. Portfolio tools (`get_portfolio_*`)
wrap each sub in try/except so partial results are returned with errors listed
separately.

**Cache on Cost Management only.** Cost queries are expensive and rate-limited
(~30 req/min per tenant). Cost data updates hourly at best. Default 15-minute
TTL trades almost nothing in freshness for significant rate-limit headroom.
Resource Graph and Advisor are fast and cheap — no caching needed.

**Structured returns, not prose.** Tools return dicts with columns/rows/metadata.
The LLM narrates them naturally. This avoids encoding English into tool responses
(which makes them brittle to prompt changes).

## Deploying to Azure (Remote Mode)

For team-wide access, deploy as a remote HTTP server:

1. **Transport swap** in `server.py`:
   ```python
   mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
   ```

2. **Dockerfile:**
   ```dockerfile
   FROM python:3.12-slim
   WORKDIR /app
   COPY . .
   RUN pip install --no-cache-dir -e .
   CMD ["azure-finops-mcp"]
   ```

3. **Deploy to Azure Container Apps** with a user-assigned managed identity.

4. **Grant RBAC** to the managed identity (same 3 roles: Cost Management Reader,
   Reader, Monitoring Reader) on each subscription.

5. **Add auth** via APIM or Azure Front Door + Entra ID.
   MCP supports OAuth for remote servers.

6. `DefaultAzureCredential` picks up the managed identity automatically —
   no code changes needed.

## Troubleshooting

| Problem | Fix |
|---|---|
| `DefaultAzureCredential` auth errors | Run `az login` and verify with `az account show` |
| 429 throttling on Cost Management | Increase `FINOPS_CACHE_TTL_SECONDS` |
| Empty budget list | Budgets must exist in the portal — the API doesn't create them |
| `find_idle_resources` errors | You need `Reader` RBAC at subscription scope |
| Inspector "Connection Error" | Use absolute path to venv's python3 in Command field |
| `print()` breaks the server | Never use `print()` in MCP tools — it corrupts the stdio JSON stream. Use `logging` instead |

#!/usr/bin/env bash
# deploy.sh — deploy Azure FinOps MCP server to Azure Container Apps
# Usage: ./deploy.sh
# Prerequisites: az login, Docker running

set -euo pipefail

# ── Config — fill these in before running ────────────────────────────────────
RESOURCE_GROUP="rg-finops-mcp"                   # Azure resource group to create/use
LOCATION="eastus"                                 # Azure region (e.g. eastus, westeurope)
ACR_NAME="<your-acr-name>"                        # must be globally unique, lowercase, no hyphens
CONTAINER_APP_NAME="azure-finops-mcp"
CONTAINER_APP_ENV="finops-mcp-env"

# Comma-separated list of Azure subscription IDs this server will have access to
ALLOWED_SUBSCRIPTIONS="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx,yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy"
# The default subscription used when none is specified in a query
DEFAULT_SUBSCRIPTION="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
# ─────────────────────────────────────────────────────────────────────────────

echo "==> Creating resource group..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "==> Creating Azure Container Registry..."
az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --sku Basic \
  --admin-enabled false \
  --output none

echo "==> Building and pushing image via ACR Tasks (no local Docker needed)..."
az acr build \
  --registry "$ACR_NAME" \
  --image "azure-finops-mcp:latest" \
  .

ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)

echo "==> Creating Container Apps environment..."
az containerapp env create \
  --name "$CONTAINER_APP_ENV" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --output none

echo "==> Deploying Container App..."
az containerapp create \
  --name "$CONTAINER_APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$CONTAINER_APP_ENV" \
  --image "$ACR_LOGIN_SERVER/azure-finops-mcp:latest" \
  --registry-server "$ACR_LOGIN_SERVER" \
  --registry-identity system \
  --target-port 8000 \
  --ingress external \
  --min-replicas 0 \
  --max-replicas 2 \
  --cpu 0.5 \
  --memory 1.0Gi \
  --env-vars \
      MCP_TRANSPORT=streamable-http \
      MCP_PORT=8000 \
      AZURE_ALLOWED_SUBSCRIPTIONS="$ALLOWED_SUBSCRIPTIONS" \
      AZURE_DEFAULT_SUBSCRIPTION="$DEFAULT_SUBSCRIPTION" \
      FINOPS_CACHE_TTL_SECONDS=900 \
  --system-assigned \
  --output none

# Get the managed identity principal ID
PRINCIPAL_ID=$(az containerapp show \
  --name "$CONTAINER_APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "identity.principalId" -o tsv)

echo "==> Granting AcrPull to Container App identity..."
ACR_ID=$(az acr show --name "$ACR_NAME" --query id -o tsv)
az role assignment create \
  --assignee "$PRINCIPAL_ID" \
  --role AcrPull \
  --scope "$ACR_ID" \
  --output none

echo "==> Granting RBAC on subscriptions to Container App identity..."
for SUB_ID in $(echo "$ALLOWED_SUBSCRIPTIONS" | tr ',' ' '); do
  echo "    Subscription: $SUB_ID"
  for ROLE in "Cost Management Reader" "Reader" "Monitoring Reader"; do
    az role assignment create \
      --assignee "$PRINCIPAL_ID" \
      --role "$ROLE" \
      --scope "/subscriptions/$SUB_ID" \
      --output none 2>/dev/null || echo "    (already assigned: $ROLE)"
  done
done

echo "==> Enabling Entra ID authentication (Easy Auth)..."
APP_URL=$(az containerapp show \
  --name "$CONTAINER_APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv)

az containerapp auth microsoft update \
  --name "$CONTAINER_APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --client-id "$(az ad app create \
      --display-name "azure-finops-mcp" \
      --sign-in-audience AzureADMyOrg \
      --web-redirect-uris "https://$APP_URL/.auth/login/aad/callback" \
      --query appId -o tsv)" \
  --issuer "https://login.microsoftonline.com/$(az account show --query tenantId -o tsv)/v2.0" \
  --output none 2>/dev/null || echo "    (Easy Auth requires manual setup in portal — see README)"

echo ""
echo "✅ Deployment complete!"
echo ""
echo "   MCP server URL: https://$APP_URL/mcp"
echo ""
echo "   Share this with your team for Claude Desktop / Cursor / VS Code:"
echo '   {'
echo '     "mcpServers": {'
echo '       "azure-finops": {'
echo '         "type": "http",'
echo "         \"url\": \"https://$APP_URL/mcp\""
echo '       }'
echo '     }'
echo '   }'
echo ""
echo "   For Claude Web: Settings → Integrations → Add → https://$APP_URL/mcp"

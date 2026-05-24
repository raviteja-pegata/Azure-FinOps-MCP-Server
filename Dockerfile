FROM python:3.12-slim

WORKDIR /app

# Install system deps needed by Azure SDK
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml ./
COPY src/ ./src/

# Install the package and dependencies
RUN pip install --no-cache-dir -e .

# Run as non-root
RUN useradd -m appuser
USER appuser

# HTTP transport for container deployment
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000

EXPOSE 8000

CMD ["python", "-m", "azure_finops_mcp.server"]

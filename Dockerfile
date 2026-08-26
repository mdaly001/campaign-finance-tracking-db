FROM python:3.11-slim

# Install uv (dependency + virtualenv manager)
RUN pip install --no-cache-dir uv

WORKDIR /app

# Create non-root user before copying code
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Copy dependency manifests and install into /app/.venv
COPY --chown=appuser:appuser pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application source
COPY --chown=appuser:appuser core/ ./core/
COPY --chown=appuser:appuser state/ ./state/
COPY --chown=appuser:appuser migrations/ ./migrations/
COPY --chown=appuser:appuser scripts/ ./scripts/

# ETL download cache + logs (zip is ~8.5 GB; mount a volume for persistence)
RUN mkdir -p /app/state/cache && chown -R appuser:appuser /app

USER appuser

ENV PATH="/app/.venv/bin:$PATH" \
    STATE_CACHE_DIR="/app/state/cache" \
    MCP_PORT="9527"

EXPOSE 9527

# Default: MCP SSE server. The db password/URL come from the environment
# (see docker-compose.yml). Run the ETL instead with:
#   docker compose run --rm etl full
#   docker compose run --rm etl incremental
CMD ["python", "-m", "core.mcp.server"]

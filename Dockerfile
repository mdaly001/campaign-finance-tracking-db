FROM python:3.11-slim

# Install uv
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy project config and install dependencies
COPY pyproject.toml .
RUN uv sync --frozen --no-dev

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser
RUN chown -R appuser:appuser /app

USER appuser

CMD ["python", "-m", "cfdb.mcp.server"]

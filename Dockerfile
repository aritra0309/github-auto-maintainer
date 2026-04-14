# ── Stage 1: builder ─────────────────────────────────────────────
FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

# ── Stage 2: runtime ─────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy source (needed for importlib.resources to find prompts/)
COPY --from=builder /build/src /app/src

# Create data directory for SQLite
RUN mkdir -p /app/data && chown appuser:appuser /app/data

USER appuser

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
ENV LOG_FORMAT=json

EXPOSE 8000
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["python", "-m", "github_auto_maintainer"]

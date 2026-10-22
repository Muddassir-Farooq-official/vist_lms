# --- Build stage: install dependencies into a venv ---
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml requirements.lock ./
COPY app ./app
# Every dependency is pinned to an exact version with hashes (requirements.lock), so a build is the same build tomorrow.
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir --require-hashes -r requirements.lock && pip install --no-cache-dir --no-deps .

# --- Runtime stage: slim image, no build toolchain ---
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV TZ=Asia/Karachi
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts ./scripts
COPY templates ./templates
COPY static ./static

RUN mkdir -p /app/var/storage && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

# Gunicorn with uvicorn workers for production; docker-compose overrides
# this command in dev for --reload.
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
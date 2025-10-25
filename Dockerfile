# syntax=docker/dockerfile:1.6

FROM python:3.11-slim AS builder
ENV PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/opt/install -r requirements.txt

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /opt/install /usr/local

COPY requirements.txt ./requirements.txt
COPY .env.template ./.env.template
COPY examples ./examples
COPY fubon_connect.py ./fubon_connect.py
COPY adapters ./adapters
COPY clients ./clients
COPY config/fubon_credentials.toml ./config/fubon_credentials.toml
COPY docs/API?‡æ?.md ./docs/API?‡æ?.md
COPY docs/API_Analysis.md ./docs/API_Analysis.md
COPY docs/PROJECT_OVERVIEW.md ./docs/PROJECT_OVERVIEW.md
COPY vnpy_fubon ./vnpy_fubon

RUN addgroup --system app && adduser --system --ingroup app app
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT}/healthz || exit 1
CMD ["bash", "-lc", "uvicorn examples.fubon_service_api:app --host 0.0.0.0 --port ${PORT:-8080}"]

# syntax=docker/dockerfile:1.7
# One image, two process types: the web server (default command) and the indexer
# (`zrw index`, from M2), run by the platform as a scheduled job.

# --- 1. the React app ------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- 2. the Python environment -------------------------------------------------------
FROM python:3.12-slim AS python
# git: zotero-rag-core is a git dependency (a tag of github.com/jmlon/zotero-rag).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# --- 3. the runtime image ----------------------------------------------------------
FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=python /app/.venv /app/.venv
COPY --from=web /web/dist /app/web/dist
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    STATIC_DIR=/app/web/dist \
    PORT=8000
USER app
EXPOSE 8000
# PORT is set by Railway, Render and Cloud Run; uvicorn must listen on it.
CMD ["sh", "-c", "exec uvicorn zotero_rag_web.app:create_app --factory --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]

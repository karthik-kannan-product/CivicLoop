# syntax=docker/dockerfile:1.7
FROM node:22.23.0-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11.13-slim-bookworm AS python-builder
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

FROM python:3.11.13-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/backend" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --system --gid 10001 civicloop \
    && useradd --system --uid 10001 --gid civicloop --home /app civicloop
WORKDIR /app
COPY --from=python-builder /app/.venv /app/.venv
COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
COPY --from=frontend /build/frontend/dist /app/frontend/dist
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod 0555 /app/docker/entrypoint.sh \
    && mkdir -p /app/backend/staticfiles \
    && python backend/manage.py collectstatic --noinput \
    && chmod -R a-w /app
USER civicloop
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]

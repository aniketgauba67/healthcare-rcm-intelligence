FROM ghcr.io/astral-sh/uv:0.9.26 AS uv

FROM python:3.11-slim-bookworm

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH="/app" \
    PYTHONDONTWRITEBYTECODE="1" \
    PYTHONUNBUFFERED="1" \
    UV_COMPILE_BYTECODE="1" \
    UV_LINK_MODE="copy"

COPY --from=uv /uv /uvx /bin/

WORKDIR /app
RUN useradd --create-home --uid 10001 app \
    && chown app:app /app

COPY --chown=app:app pyproject.toml uv.lock README.md ./

USER app

RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=app:app . .
RUN test -r dashboard/demo_data/rcm_demo.duckdb

EXPOSE 8000 8501 8502

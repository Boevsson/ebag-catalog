# syntax=docker/dockerfile:1

# --- builder: compiles dependencies into a virtualenv --------------------------
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

# mysqlclient has no Linux wheels, it is compiled against the MariaDB client library.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential pkg-config libmariadb-dev \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev


# --- dev: builder + test/lint tooling (used by `docker compose run test`) ------
FROM builder AS dev

RUN uv sync --frozen
ENV PATH="/opt/venv/bin:$PATH"
COPY . .
CMD ["pytest"]


# --- runtime: slim image without compilers --------------------------------------
FROM python:3.13-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends libmariadb3 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app . .
RUN mkdir -p /app/media && chown app:app /app/media

USER app
EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]

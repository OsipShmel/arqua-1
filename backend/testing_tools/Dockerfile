# Build from the repository root (uv workspace):
#   docker build -f backend/testing_tools/Dockerfile -t testing-tools .
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

# git: mutation test suites can come from a repository
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# Dependencies first, so code changes don't reinstall them
COPY pyproject.toml uv.lock ./
COPY backend/agent/pyproject.toml backend/agent/
COPY backend/testing_tools/pyproject.toml backend/testing_tools/
RUN uv sync --frozen --no-dev --package testing-tools --no-install-workspace

COPY backend/agent backend/agent
COPY backend/testing_tools backend/testing_tools
RUN uv sync --frozen --no-dev --package testing-tools

RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app
ENV PATH=/app/.venv/bin:$PATH \
    TT_HOST=0.0.0.0 \
    TT_PORT=8000 \
    TT_RESULTS_DIR=/data/results \
    UV_CACHE_DIR=/tmp/uv-cache
EXPOSE 8000
CMD ["testing-tools"]

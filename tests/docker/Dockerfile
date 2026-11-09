# Clean Linux stand for every test suite: the workspace is installed from uv.lock only.
#   docker compose -f docker-compose.yml -f tests/docker/compose.yml --profile test run --rm tests
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# git: the ПМИ secrets check reads `git ls-files`; mutation suites may come from git
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    LANGSMITH_TRACING=false \
    LANGCHAIN_TRACING_V2=false

WORKDIR /repo
# Dependencies first, so code changes don't reinstall them
COPY pyproject.toml uv.lock .python-version ./
COPY backend/agent/pyproject.toml backend/agent/
COPY backend/openapi-parser/pyproject.toml backend/openapi-parser/
COPY backend/testing_tools/pyproject.toml backend/testing_tools/
RUN uv sync --locked --all-packages --all-extras --no-install-workspace

COPY . .
RUN uv sync --locked --all-packages --all-extras \
    && git config --global --add safe.directory /repo

CMD ["sh", "tests/docker/run-all.sh"]

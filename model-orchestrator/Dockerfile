# Model router image.
#
#   podman build -t model-orchestrator \
#       --build-arg ROUTER_VERSION=3.0.0 --build-arg ROUTER_GIT_SHA=$(git rev-parse --short HEAD) .
#
# The image holds no secrets. Routes come from a YAML file mounted over
# /app/config.yaml, everything else from ROUTER_* environment variables
# (see README.md). Dependencies are installed from uv.lock, so two builds of
# the same commit contain the same packages.
FROM python:3.13-slim

ARG ROUTER_VERSION=0.0.0.dev0
ARG ROUTER_GIT_SHA=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# uv from PyPI, pinned; it installs exactly what uv.lock says.
RUN pip install --no-cache-dir uv==0.7.12

WORKDIR /app

# Dependencies first, so this layer is rebuilt only when the lock changes.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY model_orchestrator ./model_orchestrator
COPY migrations ./migrations
# The real config is mounted over this one at runtime. The example is valid,
# so the image starts on its own for a smoke test.
COPY config.example.yaml ./config.yaml

# Unprivileged, no home directory. The router keeps no files, but uploads
# larger than 1 MB are spooled to /tmp while they are forwarded, so /tmp must
# stay writable (mount one if the root filesystem is read-only).
RUN groupadd --system --gid 10001 router \
    && useradd --system --uid 10001 --gid router --no-create-home --home-dir /nonexistent router
USER 10001:10001

ENV PATH="/app/.venv/bin:$PATH" \
    ROUTER_CONFIG=/app/config.yaml \
    ROUTER_VERSION=${ROUTER_VERSION} \
    ROUTER_GIT_SHA=${ROUTER_GIT_SHA}

# 8090: the API. 9095: internal only (Prometheus metrics, detailed health).
EXPOSE 8090 9095

# /livez answers as long as the process runs. It does not touch the database
# or any backend, so an outage elsewhere never restarts the router.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('ROUTER_PORT','8090')+'/livez', timeout=3); sys.exit(0 if r.status==200 else 1)"]

CMD ["python", "-m", "model_orchestrator"]

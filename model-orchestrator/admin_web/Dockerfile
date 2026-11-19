# Admin console for the model orchestrator (FastAPI, server-rendered).
#
#   podman build -t mo-admin .
#   podman run --rm -p 127.0.0.1:8600:8600 --env-file .env mo-admin
#
# The image holds no secrets and no configuration: everything comes from
# ADMIN_* environment variables (see .env.example).
FROM python:3.13-slim

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

COPY mo_admin ./mo_admin

# Unprivileged, with no home directory and nothing writable: the console
# keeps no files.
RUN groupadd --system --gid 10001 console \
    && useradd --system --uid 10001 --gid console --no-create-home --home-dir /nonexistent console
USER 10001:10001

# 0.0.0.0 is right for a private container network only: publish the port on
# 127.0.0.1 and put the TLS proxy in front. With host networking set
# ADMIN_HOST=127.0.0.1 instead (see README, "Deployment behind a proxy").
ENV PATH="/app/.venv/bin:$PATH" \
    ADMIN_HOST=0.0.0.0 \
    ADMIN_PORT=8600

EXPOSE 8600

# /livez touches neither the database nor the router, so a database outage
# does not restart a console that would show a clear error page.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('ADMIN_PORT','8600')+'/livez', timeout=3); sys.exit(0 if r.status==200 else 1)"]

CMD ["python", "-m", "mo_admin"]

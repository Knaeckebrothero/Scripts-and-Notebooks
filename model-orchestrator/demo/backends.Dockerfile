# Fake model backends for the local demo stack (docker-compose.yaml).
#
#   podman build -f demo/backends.Dockerfile -t mo-demo-backends .
#
# The build context is this directory's parent (model-orchestrator/). The
# image runs scripts/demo_backends.py, which serves the test suite's fakes on
# ports 9001 to 9007. Demo only: the answers are made up.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/demo/.venv

RUN pip install --no-cache-dir uv==0.7.12

WORKDIR /demo

# The fakes are aiohttp servers. aiohttp is in the dev group, so this installs
# that group and nothing of the router, at the versions the test suite uses.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --only-group dev

COPY tests/__init__.py ./tests/__init__.py
COPY tests/fakes ./tests/fakes
COPY scripts/demo_backends.py ./scripts/demo_backends.py

USER 10001:10001
ENV PATH="/demo/.venv/bin:$PATH"

EXPOSE 9001-9007

CMD ["python", "scripts/demo_backends.py", "--host", "0.0.0.0"]

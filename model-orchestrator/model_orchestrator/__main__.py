"""Command line entry point.

python -m model_orchestrator                       serve (ROUTER_HOST / ROUTER_PORT)
python -m model_orchestrator check-config <file>   validate a config, exit non-zero on error
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from types import FrameType
from typing import TYPE_CHECKING

from .config import ConfigError, config_warnings, load_config
from .settings import Settings, SettingsError

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("model_orchestrator")
STARTUP_FAILURE = 3


def check_config(path: str) -> int:
    try:
        config = load_config(path)
    except ConfigError as exc:
        print(f"{path}: INVALID", file=sys.stderr)
        for message in exc.messages:
            print(f"  - {message}", file=sys.stderr)
        return 1
    routes, upstreams = len(config.routes), len(config.upstreams)
    aliases = sum(len(set(route.models)) for route in config.routes)
    print(f"{path}: OK ({routes} route(s), {aliases} alias(es), {upstreams} upstream(s))")
    for warning in config_warnings(config, os.environ):
        print(f"  warning: {warning}")
    return 0


def run_server(app: FastAPI, *, host: str, port: int, shutdown_grace_s: int) -> int:
    """Serve until SIGTERM or SIGINT, then stop within the grace period.

    After the signal no new connection is accepted and requests in flight get
    ``shutdown_grace_s`` seconds to finish. What is still running then is ended
    by the server; the application turns that into a proper ending for each
    client (see ``RouterState.wait_shutting_down``), records it, and exits.
    """
    import uvicorn

    class Server(uvicorn.Server):
        def handle_exit(self, sig: int, frame: FrameType | None) -> None:
            state = getattr(app.state, "router", None)
            if state is not None:
                state.note_shutdown()
            super().handle_exit(sig, frame)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=None,
        access_log=False,
        timeout_graceful_shutdown=shutdown_grace_s,
    )
    server = Server(config)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    return 0 if server.started else STARTUP_FAILURE


def serve() -> int:
    from .api import create_app
    from .logs import configure_logging

    try:
        settings = Settings.from_env()
    except SettingsError as exc:
        print(f"model-orchestrator: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level, settings.log_format)

    if not settings.database_url:
        logger.error(
            "ROUTER_DATABASE_URL is not set. API keys live in Postgres; "
            "the router cannot authenticate requests without it."
        )
        return 1
    try:
        config = load_config(settings.config_path)
    except ConfigError as exc:
        logger.error("config %s is not valid:", settings.config_path)
        for message in exc.messages:
            logger.error("  config: %s", message)
        return 1
    logger.info(
        "config loaded from %s: %d route(s), %d upstream(s)",
        settings.config_path,
        len(config.routes),
        len(config.upstreams),
    )
    for warning in config_warnings(config, os.environ):
        logger.warning("config: %s", warning)

    # ROUTER_HOST wins over the config file, so one config can serve a router
    # pinned to loopback behind a TLS proxy and one listening on all interfaces.
    host = settings.host or config.server.host or "0.0.0.0"
    if config.server.port is not None and config.server.port != settings.port:
        logger.warning(
            "config: server.port (%d) is ignored; listening on %d (set ROUTER_PORT to change it)",
            config.server.port,
            settings.port,
        )

    metrics_host = settings.metrics_host or host
    wildcard = {"0.0.0.0", "::"}
    if settings.metrics_port == settings.port and (metrics_host == host or wildcard & {host, metrics_host}):
        logger.error(
            "ROUTER_METRICS_PORT and ROUTER_PORT are both %d on overlapping addresses. The internal "
            "listener cannot share the API port; use another port, or 0 to switch it off.",
            settings.port,
        )
        return 2

    app = create_app(settings, config, listen_host=host)
    logger.info("listening on %s:%d", host, settings.port)
    return run_server(app, host=host, port=settings.port, shutdown_grace_s=settings.shutdown_grace_s)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m model_orchestrator", description="OpenAI-compatible model router"
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="run the router (the default)")
    check = commands.add_parser("check-config", help="validate a config file and exit")
    check.add_argument("file", help="path to the YAML config")
    args = parser.parse_args(argv)
    if args.command == "check-config":
        return check_config(args.file)
    return serve()


if __name__ == "__main__":
    sys.exit(main())

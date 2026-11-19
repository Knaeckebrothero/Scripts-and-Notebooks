"""``python -m mo_admin``: check the configuration, then serve."""
from __future__ import annotations

import logging
import sys

import uvicorn

from .config import ConfigError, load_settings


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per router poll is noise
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    from .main import create_app

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        # X-Forwarded-* is read by the application itself (ADMIN_TRUST_PROXY_HEADERS).
        proxy_headers=False,
        server_header=False,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

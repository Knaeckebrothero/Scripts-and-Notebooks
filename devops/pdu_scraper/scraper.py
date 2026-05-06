"""
Raritan PX2 → TimescaleDB scraper.

Polls the PDU's JSON-RPC bulk endpoint every POLL_INTERVAL seconds and inserts
per-phase readings into pdu.readings on the analytics database.

Designed to be packaged as a tiny container (python:3.12-slim + requests +
psycopg). Runs as a single Deployment replica — Recreate strategy, no need for
HA: missing a few samples during a restart is fine.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Iterable

import psycopg
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LOG = logging.getLogger("pdu-scraper")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise SystemExit(f"missing required env var: {name}")
    return v


PDU_HOST = env("PDU_HOST")
PDU_NAME = env("PDU_NAME", "rack1")
PDU_USER = env("PDU_USER")
PDU_PASS = env("PDU_PASS")
POLL_INTERVAL = int(env("POLL_INTERVAL", "30"))
HTTP_TIMEOUT = int(env("HTTP_TIMEOUT", "10"))

PG_DSN = (
    f"host={env('PG_HOST')} port={env('PG_PORT', '5432')} "
    f"dbname={env('PG_DB')} user={env('PG_USER')} password={env('PG_PASSWORD')} "
    f"sslmode={env('PG_SSLMODE', 'require')}"
)

INSERT_SQL = """
INSERT INTO pdu.readings
  (time, pdu, inlet, phase, voltage_v, current_a, active_w,
   apparent_va, power_factor, freq_hz, energy_wh)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

# JSON-RPC bulk request — fetch every sensor on inlet 0 in a single call. The
# Raritan firmware exposes /bulk for batched calls; using one bulk call is
# cheaper than 10+ individual /model/... calls and gives a consistent snapshot.
BULK_PAYLOAD = {
    "requests": [
        {
            "rid": "/model/inlet/0",
            "json": {"jsonrpc": "2.0", "method": "getSensors", "id": 1},
        },
        {
            "rid": "/model/inlet/0",
            "json": {"jsonrpc": "2.0", "method": "getInletPoles", "id": 2},
        },
    ]
}


class PDUClient:
    def __init__(self, host: str, user: str, password: str):
        self.base = f"https://{host}"
        self.session = requests.Session()
        self.session.auth = (user, password)
        self.session.verify = False  # Self-signed cert on PDU
        self.session.headers["Content-Type"] = "application/json"

    def fetch(self) -> dict:
        r = self.session.post(
            f"{self.base}/bulk",
            data=json.dumps(BULK_PAYLOAD),
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()


def _read(sensors: dict, key: str) -> float | None:
    """Pull a sensor reading out of the Raritan getSensors response.

    The exact JSON shape varies by firmware. This helper hides that — the
    scraper logs and skips fields it can't find rather than crashing, so a
    firmware quirk on one sensor doesn't lose the whole sample.
    """
    try:
        node = sensors.get(key)
        if not node:
            return None
        # Both shapes seen in the wild: {"value": X} and {"reading": X}
        if isinstance(node, dict):
            for k in ("value", "reading"):
                if k in node and node[k] is not None:
                    return float(node[k])
        elif isinstance(node, (int, float)):
            return float(node)
    except (TypeError, ValueError):
        return None
    return None


def parse(payload: dict, ts: datetime) -> Iterable[tuple]:
    """Yield (time, pdu, inlet, phase, ...) tuples ready for INSERT.

    payload is the bulk response. Two responses inside:
      [0] inlet-level sensors: voltage(L-N avg), current(total), activePower,
          apparentPower, powerFactor, frequency, activeEnergy.
      [1] per-pole sensors: one entry per phase (L1, L2, L3) with per-phase
          voltage / current / power.
    """
    responses = payload.get("responses", payload.get("results", []))

    # Inlet-level (phase 0)
    inlet_sensors = {}
    poles = []
    for resp in responses:
        body = resp.get("json", resp)
        result = body.get("result", body)
        if "sensors" in result:
            inlet_sensors = result["sensors"]
        elif "poles" in result:
            poles = result["poles"]

    yield (
        ts, PDU_NAME, 1, 0,
        _read(inlet_sensors, "voltage"),
        _read(inlet_sensors, "current"),
        _read(inlet_sensors, "activePower"),
        _read(inlet_sensors, "apparentPower"),
        _read(inlet_sensors, "powerFactor"),
        _read(inlet_sensors, "frequency"),
        int(_read(inlet_sensors, "activeEnergy") or 0) or None,
    )

    for idx, pole in enumerate(poles, start=1):
        ps = pole.get("sensors", pole)
        yield (
            ts, PDU_NAME, 1, idx,
            _read(ps, "voltage"),
            _read(ps, "current"),
            _read(ps, "activePower"),
            _read(ps, "apparentPower"),
            _read(ps, "powerFactor"),
            None,
            None,
        )


_running = True


def _stop(signum, frame):
    global _running
    LOG.info("received signal %s, stopping", signum)
    _running = False


def main() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    pdu = PDUClient(PDU_HOST, PDU_USER, PDU_PASS)
    LOG.info("scraper starting: pdu=%s host=%s interval=%ss",
             PDU_NAME, PDU_HOST, POLL_INTERVAL)

    while _running:
        started = time.monotonic()
        ts = datetime.now(tz=timezone.utc)
        try:
            payload = pdu.fetch()
            rows = list(parse(payload, ts))
            with psycopg.connect(PG_DSN, connect_timeout=10) as conn:
                with conn.cursor() as cur:
                    cur.executemany(INSERT_SQL, rows)
                conn.commit()
            LOG.debug("wrote %d rows at %s", len(rows), ts.isoformat())
        except requests.RequestException as e:
            LOG.warning("PDU fetch failed: %s", e)
        except psycopg.Error as e:
            LOG.error("DB write failed: %s", e)
        except Exception:
            LOG.exception("unexpected error during poll")

        # Sleep the remainder of the interval; never a tight loop on errors.
        elapsed = time.monotonic() - started
        time.sleep(max(1.0, POLL_INTERVAL - elapsed))

    LOG.info("scraper stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

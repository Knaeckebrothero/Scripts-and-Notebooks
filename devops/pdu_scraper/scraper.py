"""
Raritan PX2/PX3 → TimescaleDB scraper.

Polls the PDU's JSON-RPC API every POLL_INTERVAL seconds and inserts per-phase
readings into pdu.readings on the analytics database.

Raritan's RPC model is reference-based, not value-inlined:

  /model/pdu/0  .getInlets()            -> [ {rid: <inlet>, type} ]
  <inlet>       .getSensors()           -> { voltage: {rid,type}|null, current: ..., ... }
  <inlet>       .getPoles()             -> [ { line, voltage:{rid}, voltageLN:{rid}, current:{rid}, ... } ]
  <sensor rid>  .getReading()           -> { value, valid, available, timestamp, status }

So we discover the sensor rids once (the topology is stable), cache them, and
then each poll just calls getReading() on each cached rid. The cache is
refreshed periodically and whenever a poll fails, so a firmware reboot or
topology change self-heals.

Designed to be packaged as a tiny container (python:3.12-slim + requests +
psycopg). Runs as a single Deployment replica — Recreate strategy, no need for
HA: missing a few samples during a restart is fine.
"""

from __future__ import annotations

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
# Re-discover the sensor topology every N polls (cheap insurance against a
# firmware reboot renaming rids). At 30s/poll, 120 ≈ once an hour.
REDISCOVER_EVERY = int(env("REDISCOVER_EVERY", "120"))

# Root object that exposes the inlet list. Stable across PX2/PX3 firmware.
PDU_ROOT_RID = "/model/pdu/0"

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

# Column order for pdu.readings, after the (time, pdu, inlet, phase) prefix.
# Maps each numeric column to the Raritan sensor member that feeds it.
INLET_COLUMNS = [
    ("voltage_v", "voltage"),
    ("current_a", "current"),
    ("active_w", "activePower"),
    ("apparent_va", "apparentPower"),
    ("power_factor", "powerFactor"),
    ("freq_hz", "lineFrequency"),   # PX3 firmware calls it lineFrequency, not frequency
    ("energy_wh", "activeEnergy"),
]
# Per-pole, only voltage + current are populated on this hardware; power/PF/
# energy members exist in the model but read null, so we leave those columns
# NULL for phase rows. voltageLN (line-to-neutral) is the meaningful per-phase
# voltage; fall back to voltage (line-to-line) if LN isn't exposed.
POLE_COLUMNS = [
    ("voltage_v", ("voltageLN", "voltage")),
    ("current_a", ("current",)),
]


class PDUError(RuntimeError):
    pass


class PDUClient:
    def __init__(self, host: str, user: str, password: str):
        self.base = f"https://{host}"
        self.session = requests.Session()
        self.session.auth = (user, password)
        self.session.verify = False  # Self-signed cert on PDU
        self.session.headers["Content-Type"] = "application/json"

    def call(self, rid: str, method: str):
        """Invoke a JSON-RPC method on an rid, returning the _ret_ payload."""
        r = self.session.post(
            f"{self.base}{rid}",
            json={"jsonrpc": "2.0", "method": method, "id": 1},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise PDUError(f"{method} on {rid}: {data['error']}")
        return data.get("result", {}).get("_ret_")

    def read_value(self, rid: str) -> float | None:
        """getReading on a NumericSensor rid -> float, or None if unavailable."""
        ret = self.call(rid, "getReading")
        if not isinstance(ret, dict):
            return None
        if not ret.get("available", True) or not ret.get("valid", True):
            return None
        val = ret.get("value")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None


def _rid_of(node) -> str | None:
    """Sensor members are {rid, type} or null."""
    if isinstance(node, dict):
        return node.get("rid")
    return None


def discover(client: PDUClient) -> list[dict]:
    """Build the poll plan: one entry per (inlet, phase) with its sensor rids.

    Returns a list of {inlet, phase, rids: {column: rid}} dicts. Inlets are
    numbered from 1; phase 0 is the inlet total, phases 1..N are the poles
    (L1, L2, L3). Poles with no readable sensors (e.g. the neutral) are skipped.
    """
    plan: list[dict] = []
    inlets = client.call(PDU_ROOT_RID, "getInlets") or []
    for inlet_idx, inlet in enumerate(inlets, start=1):
        irid = _rid_of(inlet)
        if not irid:
            continue

        # Inlet total (phase 0)
        sensors = client.call(irid, "getSensors") or {}
        rids = {col: _rid_of(sensors.get(member)) for col, member in INLET_COLUMNS}
        rids = {c: r for c, r in rids.items() if r}
        if rids:
            plan.append({"inlet": inlet_idx, "phase": 0, "rids": rids})

        # Per-pole rows (phases 1..N)
        poles = client.call(irid, "getPoles") or []
        for pole in poles:
            prids: dict[str, str] = {}
            for col, members in POLE_COLUMNS:
                for member in members:
                    rid = _rid_of(pole.get(member))
                    if rid:
                        prids[col] = rid
                        break
            if not prids:
                continue  # neutral / unpopulated pole
            phase = int(pole.get("line", len(plan))) + 1
            plan.append({"inlet": inlet_idx, "phase": phase, "rids": prids})

    if not plan:
        raise PDUError("discovery produced no sensors")
    LOG.info("discovered %d sensor groups across %d inlet(s)",
             len(plan), len(inlets))
    return plan


def build_rows(client: PDUClient, plan: list[dict], ts: datetime) -> Iterable[tuple]:
    """Read every cached rid and yield INSERT-ready tuples."""
    for entry in plan:
        rids = entry["rids"]
        vals = {col: client.read_value(rid) for col, rid in rids.items()}
        energy = vals.get("energy_wh")
        yield (
            ts, PDU_NAME, entry["inlet"], entry["phase"],
            vals.get("voltage_v"),
            vals.get("current_a"),
            vals.get("active_w"),
            vals.get("apparent_va"),
            vals.get("power_factor"),
            vals.get("freq_hz"),
            int(energy) if energy is not None else None,
        )


_running = True


def _stop(signum, frame):
    global _running
    LOG.info("received signal %s, stopping", signum)
    _running = False


def main() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    client = PDUClient(PDU_HOST, PDU_USER, PDU_PASS)
    LOG.info("scraper starting: pdu=%s host=%s interval=%ss",
             PDU_NAME, PDU_HOST, POLL_INTERVAL)

    plan: list[dict] | None = None
    polls_since_discovery = 0

    while _running:
        started = time.monotonic()
        ts = datetime.now(tz=timezone.utc)
        try:
            if plan is None or polls_since_discovery >= REDISCOVER_EVERY:
                plan = discover(client)
                polls_since_discovery = 0

            rows = list(build_rows(client, plan, ts))
            with psycopg.connect(PG_DSN, connect_timeout=10) as conn:
                with conn.cursor() as cur:
                    cur.executemany(INSERT_SQL, rows)
                conn.commit()
            polls_since_discovery += 1
            LOG.debug("wrote %d rows at %s", len(rows), ts.isoformat())
        except (requests.RequestException, PDUError) as e:
            LOG.warning("PDU fetch failed (will re-discover): %s", e)
            plan = None  # force fresh discovery next poll
        except psycopg.Error as e:
            LOG.error("DB write failed: %s", e)
        except Exception:
            LOG.exception("unexpected error during poll")
            plan = None

        # Sleep the remainder of the interval; never a tight loop on errors.
        elapsed = time.monotonic() - started
        time.sleep(max(1.0, POLL_INTERVAL - elapsed))

    LOG.info("scraper stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

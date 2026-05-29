# pdu-scraper

Polls a Raritan PX2/PX3 PDU over its JSON-RPC API and inserts per-phase power
readings into a TimescaleDB hypertable. Single-replica, ~250 lines of Python.

Deployed in the homelab cluster from
[`HomeLab/deployments_unmanaged/pdu-scraper/`](https://github.com/Knaeckebrothero/HomeLab/tree/main/deployments_unmanaged/pdu-scraper).
This directory is the **image source** — schema, manifests, and operational
docs live with the deployment.

## How it works

Raritan's RPC model is **reference-based**, not value-inlined — `getSensors`
hands back sensor *rids*, and you read each one separately:

```
/model/pdu/0   .getInlets()    -> [ {rid: <inlet>} ]
<inlet>        .getSensors()   -> { voltage: {rid}, current: {rid}, activePower: {rid}, ... }
<inlet>        .getPoles()      -> [ { line, voltageLN: {rid}, current: {rid}, ... } ]
<sensor rid>   .getReading()   -> { value, valid, available }
```

The scraper discovers the inlet/sensor/pole rids once, caches the topology
(re-discovering every `REDISCOVER_EVERY` polls and on any error, so a PDU
reboot self-heals), then each poll calls `getReading()` on every cached rid.

> The `/bulk` batch endpoint is **not usable** on the tested firmware
> (PX3 3.3.10 returns `-32700 "element does not exist"` regardless of rid), so
> reads are sequential. At ~16 calls/poll that still finishes in ~5s, well
> inside the 30s interval.

## Image

`ghcr.io/knaeckebrothero/pdu-scraper:latest` — built via GHA on push to `main`
when anything under `devops/pdu_scraper/` changes (see
`.github/workflows/pdu-scraper.yml`).

## Required environment

| Var | Notes |
| --- | --- |
| `PDU_HOST` | PDU IP / hostname |
| `PDU_USER` | PDU CLI user (typically `admin`) |
| `PDU_PASS` | PDU password |
| `PDU_NAME` | Tag for the `pdu` column, default `rack1` |
| `POLL_INTERVAL` | Seconds, default `30` |
| `HTTP_TIMEOUT` | Per-request timeout, default `10` |
| `REDISCOVER_EVERY` | Re-walk the sensor topology every N polls, default `120` (~1h at 30s) |
| `PG_HOST` | TimescaleDB host |
| `PG_PORT` | default `5432` |
| `PG_DB` | typically `analytics` |
| `PG_USER` | dedicated role, typically `pdu_scraper` |
| `PG_PASSWORD` | role password |
| `PG_SSLMODE` | default `require` |
| `LOG_LEVEL` | default `INFO`; `DEBUG` prints write counts |

## Local test

```bash
podman build -t pdu-scraper:dev .
podman run --rm \
  -e PDU_HOST=10.0.50.30 -e PDU_USER=admin -e PDU_PASS=... \
  -e PG_HOST=10.0.51.15 -e PG_DB=analytics \
  -e PG_USER=pdu_scraper -e PG_PASSWORD=... \
  -e LOG_LEVEL=DEBUG \
  pdu-scraper:dev
```

## Schema

See `HomeLab/deployments_unmanaged/pdu-scraper/10-schema.sql`. The scraper
expects `pdu.readings` (hypertable) with columns
`(time, pdu, inlet, phase, voltage_v, current_a, active_w, apparent_va,
power_factor, freq_hz, energy_wh)`.

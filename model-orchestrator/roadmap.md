# Roadmap

What is done, what happens next, and what is deliberately left for later. The design behind version 3 is
`docs/superpowers/specs/2026-11-18-model-router-rework-design.md` in this repository; section numbers
below refer to it. The admin website's decisions are in `docs/superpowers/specs/2026-11-19-admin-website-design.md`.

## Where things stand

- **Version 3 of the router is merged to `main` (2026-11-19) and not yet released.** It is the package
  `model_orchestrator/`, with unit tests, an end-to-end contract suite and a CI pipeline that gates
  publishing. `pyproject.toml` says `3.0.0`; the first build of `main` was `sha-8464a9e`.
- Two kinds of site exist, and the plan below treats them differently:
  - *Site A*: one GPU host, rootless Podman with systemd units, updates itself from the `latest` tag.
    **Still runs 2.x.**
  - *Site B*: Kubernetes, image tags pinned in git. The pilot site: **runs version 3 (`sha-8464a9e`)
    and the admin website with single sign-on since 2026-11-19.**
- **The admin website (`admin_web/`) replaced the Streamlit page at site B.** Site A still uses the
  Streamlit page (`admin_ui/`).
- **The 2.x router (`main.py`, `store.py`) is still in the repository**, only so that the contract suite
  can run against it (`ROUTER_TARGET=legacy`). The image no longer contains it.

## Done in version 3

| Area | What |
|---|---|
| Structure | A package of small modules with no state at import time; `uv.lock`; ruff, mypy (strict), unit tests, the contract suite on Postgres 16 and 17 |
| CI and releases | Every image is tested before it is published. `latest` and `vX.Y.Z` move on a release tag only; `main` publishes `sha-<short>` |
| Fixes clients can see | Real status codes for streaming errors; the backend's own status and message; 504 for timeouts; 502 for broken connections; rerank `top_n` and all speech-to-text form fields forwarded; the error envelope everywhere; cancelled backend calls when the client leaves (section 6, C1 to C22) |
| Availability | Active health probes decide which models are listed and which requests get an immediate 503; `ROUTER_HEALTH_GATING=off` as the escape hatch |
| Accounting | Token counts per key and model in `usage_hourly`, including streamed answers; outcomes per request; `usage_daily` unchanged |
| Limits | Per-key concurrency cap next to the per-minute limit, and an optional default cap for keys without one |
| Resilience | Probes and discovery on connections of their own; 503 `router_busy` instead of a queue when the backend connections run out; streaming clients that stop reading are dropped; a shutdown ends requests in flight with an answer |
| Operations | Prometheus metrics and a detailed health view on an internal port; one log line per request; validated config with `check-config`; reload on file change and `SIGHUP`; database migrations applied at start |
| Several sites | Upstream-router mode: one site uses another site's models through its router, with one key |
| Around the code | README, example config, a local demo stack without a GPU (`docker-compose.yaml`), deployment files for Quadlet and Kubernetes, the live smoke script (`scripts/smoke.py`) |
| Admin website | Keys, usage with tokens, route status, audit log; sign-in with OpenID Connect or a local account; sign-out that holds across restarts |

## Next: the rollout

No step replaces a running router in one go, and no step needs both sites to change at the same time.

### 1. Pilot at site B, by `sha-` pin

**Status 2026-11-19:** steps 1 to 4 done (merged, `check-config` passed, `sha-8464a9e` pinned, smoke
test passed for every route). Step 5, watching for a few days, is running.

1. Merge to `main`. CI publishes `sha-<short>`; `latest` does not move, so site A is not touched.
2. Run `check-config` from that image on site B's live config.
3. Pin the `sha-` tag at site B.
4. Run `scripts/smoke.py` against it. Compare `/health` with what is known to be running.
5. Scrape the internal port and watch for a few days: `router_backend_up`, `router_requests_total` by
   outcome, `router_usage_records_dropped_total`, `router_internal_errors_total`.
6. Fix what turns up, pin the next `sha-` build, repeat.

Done when site B has run one build for several days without an error caused by the router and without
health gating taking a working backend offline.

### 2. Release 3.0.0, which upgrades site A

Site A follows `latest`, so the release is its upgrade. Before the tag:

- [ ] Site A's live config passes `check-config` with the release candidate image.
- [ ] Every probed backend at site A reports healthy on the running router. A `health_path` that fails
      today would take its model offline under version 3: fix the path, or set it to `null`.
- [ ] Nothing at site A scrapes `GET /metrics` on the API port or parses the old `/health`.
- [ ] Port 9095 is free on the host and not published.
- [ ] Any change freeze at site A is over.

Then:

1. Set `version = "3.0.0"` in `pyproject.toml` and run `uv lock` (the lockfile records the version).
2. Tag `model-orchestrator-v3.0.0`. CI publishes `v3.0.0` and moves `latest`.
3. Site A pulls it at its next auto-update, or by hand: `podman pull`, then restart the unit.
4. Run `scripts/smoke.py` against site A.
5. Afterwards, at leisure: copy the new Quadlet unit (health command on `/livez`), add health paths and
   concurrency caps where they help. None of this is needed for the upgrade itself.

If it goes wrong: pin the previous image in the unit and restart. The database needs no rollback,
because the migrations only add; `test_the_old_router_still_runs_on_a_migrated_database` proves that 2.x
runs on a database version 3 has migrated. If only health gating misbehaves, `ROUTER_HEALTH_GATING=off`
is enough.

### 3. Site B uses site A through an upstream entry

Today site B keeps a copy of site A's route list, synced by hand. With both sites on version 3:

1. Create a key for site B in site A's router.
2. Replace the copied routes in site B's config with one `upstreams` entry.
3. Check that key allowlists at site B still name the right routes: the route names now come from
   site A's `x_router` and should be the same names as before.
4. Run the smoke script against site B.

### 4. Roll out the admin website

**Status 2026-11-19:** done at site B, with single sign-on from the start and the old Streamlit login as
the local break-glass account. Site A follows after its router upgrade.

1. Deploy `admin_web` next to the Streamlit page (`deployment/admin-web.container`, or
   `deployment/kubernetes/admin-web.yaml`), first with the local account.
2. Register it with the identity provider and switch on single sign-on.
3. Point the public UI port at it (`UI_UPSTREAM` in the nginx unit).
4. Retire `admin_ui/` (see the clean-up list).

## Clean-up after the rollout

Only once no site runs 2.x any more.

- [ ] Delete `main.py` and `store.py`, and their entries in the ruff `extend-exclude` list in
      `pyproject.toml`.
- [ ] Remove the legacy test target: the `ROUTER_TARGET` switch in `tests/harness/router.py` and
      `tests/conftest.py`, and the `legacy` column in `tests/README.md`.
- [ ] Delete the tests marked `legacy_only`. They pin 2.x behaviour that version 3 changed on purpose.
- [ ] Turn the `change(...)` and `feature(...)` markers into plain tests: without a second target they
      no longer select anything.
- [ ] `tests/e2e/test_upstream.py::test_old_router_as_upstream_has_no_x_router` starts `main.py` as the
      upstream. Replace it with a small stub that lists models without `x_router`, or delete it when no
      2.x router can be an upstream any more.
- [ ] Keep `tests/harness/legacy_schema.sql` for as long as a database created by 2.x exists somewhere:
      it is what proves that such a database is adopted in place.
- [ ] Retire `admin_ui/`: the directory, `deployment/admin-ui.container` and `admin-ui.env.example`,
      its image in `.github/workflows/model-orchestrator.yml`, and the mentions in the nginx files and
      the README.
- [ ] Drop "Upgrading from 2.x" from the README when nobody needs it.

## Follow-ups

Separate pieces of work, in no fixed order (section 12 of the design, plus what the build turned up).

| Item | Why | Starting point |
|---|---|---|
| Scheduled synthetic check | Notice a broken model before a user does | `scripts/smoke.py --json`, run on a timer, result pushed to the monitoring |
| Dashboard and alert rules | The metrics exist; nothing shows them yet | A Grafana dashboard and Prometheus rules under `deployment/` |
| Dependency updates for workflow actions | Python packages and base images get weekly Dependabot pull requests (`.github/dependabot.yml`); the actions in the workflow are pinned and will age | Add the `github-actions` updater once no workflow in the repository moves an auto-updated `latest` on every push to `main`; until then an action bump in another workflow would roll a production image |
| Database backups | Keys and usage history exist once | A deployment task at each site |
| Health-gated start at site A | A bad image should roll back by itself | `podman auto-update` rolls back when the unit fails to become healthy; needs a start that reports health to systemd |
| Model servers accept the router only | Today a client inside the network can bypass keys and limits | Firewall or bind addresses at each site |
| Token budgets per key | Counting came first; limits can follow now that there are numbers | `usage_hourly` |
| Admin API | The admin website talks to the database directly | An API in the router would let it run without database access |
| Load-aware balancing | Round robin ignores how busy a backend is | Needs a load signal from the backends |
| More than one replica | Limiter and health state are in memory | Needs a shared store; not needed at today's traffic |
| A clear error for uploads without a writable `/tmp` | With a read-only root filesystem and no `/tmp`, an upload above 1 MB ends as a plain 500 | Catch it in the multipart handler, or check at start |

## Not planned

- **Translating between API dialects.** The router forwards. That is the point of it.
- **New endpoints for their own sake.** `/v1/completions`, `/v1/responses` and image endpoints are added
  when a backend and a user need them. It is one entry in the endpoint table plus tests.
- **GPU scheduling, planned availability windows, model swapping.** Another tool's job.
- **A rewrite in another language.** The load is small, and the contract suite makes the Python code safe
  to change.

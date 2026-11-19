# Router test suite

Unit tests (`tests/unit/`, no network and no database) and the end-to-end
contract suite (`tests/e2e/`). In the contract suite the router runs as a black
box (a subprocess, spoken to over HTTP) against fake backends and a real
Postgres. It is the definition of the client contract in
`docs/superpowers/specs/2026-11-18-model-router-rework-design.md` (sections 5 to 8).

## Run it

```bash
cd model-orchestrator
uv run pytest                         # everything under tests/
uv run pytest tests/unit              # the unit tests only: about a second, nothing else needed
uv run pytest tests/e2e tests/fakes   # the end-to-end suite and the fakes' self-tests
uv run pytest tests/e2e/test_streaming.py -k byte
ROUTER_TARGET=legacy uv run pytest tests/e2e   # the same suite against the 2.x router (main.py)
```

The end-to-end suite needs `uv`, and either `podman` with the
`postgres:17-alpine` image or a Postgres you point it at. The TLS tests of the
smoke script also want the `openssl` command and are skipped without it.
Nothing leaves 127.0.0.1.

| Variable | Default | Meaning |
|---|---|---|
| `ROUTER_TARGET` | `new` | Which router to test: `new` (the package) or `legacy` (the 2.x router), see below |
| `ROUTER_CMD` | – | Replaces the start command of the `new` target (shell-style string, run in `model-orchestrator/`) |
| `TEST_DATABASE_URL` | – | Use this Postgres instead of starting one. The role needs `CREATEDB`: every router instance gets its own `mo_e2e_<random>` database, dropped afterwards |
| `TEST_POSTGRES_IMAGE` | `docker.io/library/postgres:17-alpine` | Image for the throwaway Postgres (use `postgres:16-alpine` for the second supported version) |
| `E2E_BIG_BODY_MB` | `32` | Size of the "large body passes" requests. The contract says 200; set it for a slow, memory-hungry full check |
| `E2E_SLOW` | – | Set to `1` to also run the two tests that wait a full minute (a 60 s read timeout, a whole rate-limit window) |

Without `TEST_DATABASE_URL` the suite starts `podman run --rm` on a free port
(container `mo-e2e-pg-<random>`, data on tmpfs) and removes it at the end. If a
run is killed hard, `podman rm -f $(podman ps -aq --filter label=mo-e2e=1)` cleans up.

## The target switch

`ROUTER_TARGET=new`, the default, starts `python -m model_orchestrator`: this
is what ships and what CI runs. `ROUTER_TARGET=legacy` starts `python main.py`,
the 2.x router. It stays in the repository only for this comparison and goes
once no site runs 2.x any more (see `../roadmap.md`).

Both get the same environment (see `harness/router.py`): a generated YAML
config, their own database, a free port, and fast settings (key refresh 1 s,
unhealthy mark 2 s, discovery 2 s, probe interval 1 s, two failed probes mean
down, config poll 1 s, shutdown grace 1 s, metrics on a free port).

Markers decide what a test means for each target:

| Marker | `new` | `legacy` |
|---|---|---|
| none | must pass. This is the contract (design section 5) | must pass |
| `@pytest.mark.change("C7")` | must pass. The NEW behaviour of a row in section 6 | strict xfail |
| `@pytest.mark.legacy_only` | skipped. Pins the "Today" column of section 6 | must pass |
| `@pytest.mark.feature("name")` | must pass. Section 7 behaviour without a change id | skipped |

A change test on legacy must fail *with an assertion*. If it passes, the "Today"
column of the design is wrong and the run fails (`XPASS(strict)`). If it dies
with any other exception, that is a bug in the test and the run fails too. So
write change tests so that the old behaviour trips an `assert`: use
`error_of(response)`, `.get("code")` and `wait_until(...)` rather than code that
raises `KeyError` or a transport error.

`uv run pytest -m change --runxfail --tb=line` shows why each change test fails
on legacy. `tests/e2e/test_meta.py` fails if a row C1 to C22 has no test.

## Layout

```
tests/
  conftest.py        target switch, fixtures, router logs on failure
  unit/              unit tests of the package, one file per module
  fakes/             fake backends (real HTTP servers), their self-tests
  harness/           Postgres, router process, "stack" = router + database + key + client
  e2e/               the contract suite
  fixtures/          sanitised copies of two live route configs, which must keep loading
```

### Fixtures

- `stack`, `world`: the shared default router with seven routes and its fakes
  (`harness/world.py`). `world` resets every fake before and after each test.
  Most tests use these.
- `factory` (per test) and `module_factory` (per module): build your own with
  `factory.fake("vllm", ["acme/model"])` and `factory.stack([route(...)], env={...})`.
  Use them whenever a test stops a backend, fails its health check, needs other
  settings or a special config. Never do that to the shared `world`: on the new
  router a backend that was seen down stays unavailable until the next probe.
- A stack gets a database prepared the way the old router left it
  (`harness/legacy_schema.sql`) and one default key seeded before the start, so
  every run also exercises "adopt an existing database". `schema=None` starts on
  an empty database.
- Keys are created by SQL: `stack.db.create_key(rpm=3, allowed=[...])`, then
  `stack.sync_keys()` (or `stack.new_key(...)` for both). `sync_keys` inserts a
  sentinel key and waits until the router accepts it.

### Fakes

`FakeVLLM` (chat, SSE with `reasoning`, the usage-only event when
`stream_options.include_usage` is set, `/v1/models` with `max_model_len`,
`/health`), `FakeTEI` (embeddings, 422 above 64 inputs), `FakeReranker`,
`FakeWhisper` (echoes the form), `FakeKokoro` (bytes and voices),
`FakeForwardProxy` (for `proxy:`), `TcpRelay` (to take Postgres away from one
router; with `tls=` it is an HTTPS front for a router).

A fake listens on 127.0.0.1 on a free port. `scripts/demo_backends.py` runs the
same fakes on fixed ports for the demo stack, by setting `fake.host` and
`fake.port` before `start()`.

Every request is recorded in `fake.requests` (`fake.last(path)`, `fake.calls(path)`):
headers, body, JSON, form fields, the bytes sent back, and `aborted` when the
caller hung up first. Faults: `delay`, `fail(status, body)`, `hang`, `drop(after=n)`,
`reset_connection`, `health_status = 503`, `stop()` / `start()`, `tarpit()`
(connects hang), `pause_stream_after(n)` / `resume_stream()`.

Responses are a pure function of the request, so expected bytes are computed,
not copied: `FakeVLLM.render_stream(payload)`, `FakeVLLM.render_completion(payload)`.

## When a test fails

The report has a section `router log: <name>` per running router with the lines
written during that test. The full logs and configs stay in pytest's temp
directory (`/tmp/pytest-of-$USER/pytest-current/router-*/`).

## Timing

Everything under `tests/` takes about four minutes on the default target; a
legacy run of the contract suite takes about two. Three places wait on purpose:
the legacy router re-runs discovery only 15 s after its start
(`test_availability.py`), the connect timeout is a fixed 10 s
(`test_timeouts.py`), and the database outage test watches for 3.5 s.
Everything else polls with a deadline; there are no
fixed sleeps that a slow machine could outrun, except the "nothing changes for
n seconds" checks (`holds_for`).

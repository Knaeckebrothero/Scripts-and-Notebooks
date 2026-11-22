# Model orchestrator admin console

A web console for the model router in this directory's parent: create and manage API keys, see who
uses what, check which routes are up, and read back who changed what. It replaces the Streamlit page in
`../admin_ui/`, which keeps working next to it.

Server-rendered FastAPI and Jinja2, htmx for partial updates, inline SVG charts. No Node build step and
nothing loaded from a CDN. Why it is built this way:
[`docs/superpowers/specs/2026-11-19-admin-website-design.md`](../../docs/superpowers/specs/2026-11-19-admin-website-design.md).

| Page | What it shows |
|---|---|
| Overview | Requests, errors, tokens and active keys for 24 h, 7 d or 30 d; requests over time by route; route status; busiest keys; keys that are expired, about to expire or never used |
| Keys | Searchable, sortable list; create (the secret is shown once), edit, enable or disable, delete with typed confirmation |
| Key detail | Requests and tokens per day, by route, by model id and by outcome; settings; change history |
| Usage | Any date range, grouped by key, owner, route, model or outcome; totals or per day; CSV export |
| Models | Routes with their model ids and live up/down status from the router |
| Audit | Every change and every sign-in |

## Run it locally

Needs [uv](https://docs.astral.sh/uv/) and a Postgres the router has started against. uv installs
Python 3.13 itself (`.python-version`).

```bash
cd model-orchestrator/admin_web
cp .env.example .env            # set ADMIN_DATABASE_URL and ADMIN_LOCAL_PASSWORD at least
set -a; . ./.env; set +a
uv run python -m mo_admin       # http://127.0.0.1:8600
```

No database at hand? Start a throwaway one and fill it with invented data:

```bash
podman run -d --rm --name mo-admin-dev -e POSTGRES_PASSWORD=devonly -e POSTGRES_DB=orchestrator \
  -p 127.0.0.1:5433:5432 docker.io/library/postgres:17-alpine
export ADMIN_DATABASE_URL=postgresql://postgres:devonly@127.0.0.1:5433/orchestrator
uv run python scripts/seed_demo.py --migrate --reset     # 12 keys, 60 days daily, 14 days hourly
uv run python -m tests.fakes router 8612 &               # optional: a fake router for live status
ADMIN_ROUTER_URL=http://127.0.0.1:8612 ADMIN_LOCAL_PASSWORD=dev-password-123 uv run python -m mo_admin
```

`seed_demo.py` is for development databases only. It refuses to run when keys exist unless `--reset` is
given, and `--reset` empties the tables. `--no-hourly` leaves `usage_hourly` empty, which is what an
install looks like before the router writes hourly usage. `--migrate` applies `../migrations/*.sql`; in a
real installation the router does that and the console never touches the schema. If tables or columns
are missing, the console says which ones on a "schema is not initialised" page. That includes
`admin_revoked_sessions` from migration `0005`, which this console needs for sign-out.

## Configuration

Everything is an environment variable; `.env.example` lists them all with comments. A bad value stops
the start with a message that names the variable.

| Variable | Default | Meaning |
|---|---|---|
| `ADMIN_DATABASE_URL` | required | Postgres of the router |
| `ADMIN_ROUTER_URL` | unset | Router base URL for `GET /health` and `GET /v1/models`; without it live status is off |
| `ADMIN_SESSION_SECRET` | random per start | Signs the session cookie, 32 characters or more |
| `ADMIN_SESSION_HOURS` / `ADMIN_SESSION_IDLE_MINUTES` | 8 / 60 | Absolute (at most 72) and idle session lifetime |
| `ADMIN_OIDC_ISSUER`, `ADMIN_OIDC_CLIENT_ID`, `ADMIN_OIDC_CLIENT_SECRET` | unset | OpenID provider; the issuer switches single sign-on on |
| `ADMIN_OIDC_REQUIRED_ROLE` | required with OIDC | Role an account needs |
| `ADMIN_OIDC_INTERNAL_URL` | unset | The issuer as the console itself reaches it, when that is not the address browsers use |
| `ADMIN_OIDC_ROLE_CLAIM` | `realm_access.roles` | Dotted path to the roles in the token |
| `ADMIN_LOCAL_USER` / `ADMIN_LOCAL_PASSWORD` | `admin` / unset | Local account; active once a password is set. Fewer than 12 characters stops the start |
| `ADMIN_LOCAL_LOGIN` | `on` | `off` removes the local account entirely |
| `ADMIN_LOGIN_MAX_ATTEMPTS` / `ADMIN_LOGIN_LOCKOUT_SECONDS` | 5 / 60 | Failed attempts before a source address is locked out, and for how long at first |
| `ADMIN_PUBLIC_URL` | unset | External URL behind a proxy, used for the OIDC redirect URI and Secure cookies |
| `ADMIN_TRUST_PROXY_HEADERS` | `on` | Believe `X-Forwarded-Proto`, `-Host` and `-For` |
| `ADMIN_HOST` / `ADMIN_PORT` | `127.0.0.1` / `8600` | Listen address |

## Sign-in

**Single sign-on** is an authorization-code flow with PKCE (S256). The ID token is checked in full:
signature against the provider's JWKS, issuer, audience and `azp`, expiry, and the nonce sent with the
request. Only asymmetric algorithms are accepted. An account without the required role gets a 403 page
that names the role.

Keycloak, in short:

1. Clients → Create client: OpenID Connect, client ID `model-orchestrator-admin`, client authentication
   on, standard flow only.
2. Valid redirect URIs: `https://admin.example.org/auth/oidc/callback`. Valid post logout redirect URIs:
   `https://admin.example.org/login*`.
3. Advanced → Proof Key for Code Exchange: `S256`.
4. Realm roles → create `router-admin` and assign it to the administrators, directly or through a group.
5. Credentials → copy the client secret into `ADMIN_OIDC_CLIENT_SECRET`. The issuer is
   `https://<keycloak>/realms/<realm>`.

Keycloak sends realm roles in the access token, not the ID token. The console looks in the ID token
first, then in the access token (after verifying it the same way), then at the userinfo endpoint, so the
default client scopes work unchanged. For client roles set
`ADMIN_OIDC_ROLE_CLAIM=resource_access.model-orchestrator-admin.roles`.

Things to know:

- The role is checked at sign-in. Taking it away at the provider ends access when the session ends, at
  the latest after `ADMIN_SESSION_HOURS`; to cut someone off at once, also rotate `ADMIN_SESSION_SECRET`.
- Sign-out sends the browser to the provider's end-session endpoint with `client_id` and
  `post_logout_redirect_uri`, without an `id_token_hint` (the ID token is not kept). Keycloak therefore
  asks the user to confirm before it ends its own session. The console's session is over either way.
- The issuer may be written with or without a trailing slash. Tokens must carry exactly the issuer the
  provider's discovery document states.
- `ADMIN_OIDC_INTERNAL_URL` is for a provider the console reaches at another address than browsers do,
  for example Keycloak on the same host behind a TLS proxy whose certificate the console cannot verify
  (`ADMIN_OIDC_INTERNAL_URL=http://127.0.0.1:8601/realms/<realm>`). The console's own requests
  (discovery, keys, token, userinfo) to addresses under the issuer go there instead; an endpoint the
  provider keeps elsewhere is used as stated. Browsers are still sent to the issuer, and the discovery
  document and the tokens must still name the issuer. Plain http is accepted for localhost only.
- A refused sign-in (role missing) is written to the audit log once per account per ten minutes, however
  often it is repeated.

To try the flow without a Keycloak: `uv run python -m tests.fakes provider 8613` prints an issuer, client
id and secret for a local stand-in with one user.

**The local account** is for development and for the day the identity provider is down, so nobody but
its owner may be able to shut it. Comparison is constant-time, and there are two brakes:

- A source address that fails `ADMIN_LOGIN_MAX_ATTEMPTS` times in a row is locked out for
  `ADMIN_LOGIN_LOCKOUT_SECONDS`, doubling up to 15 minutes. Only that address is affected. The address is
  the last `X-Forwarded-For` entry, the one your own proxy adds.
- Failures from all addresses together (four times that number within ten minutes) slow every attempt
  down, by up to 4 seconds each. This is a delay, never a refusal: the right password still gets in.

Guessing is slowed, not stopped, so the password has to be strong: the console refuses to start with one
shorter than 12 characters. Use a long random one (`openssl rand -base64 24`) and keep it where the
break-glass procedure says, or set `ADMIN_LOCAL_LOGIN=off`.

## Security notes

- **Secrets of keys.** A key is `sk-mo-` plus 40 hex characters. Only its SHA-256 and its first 12
  characters are stored, exactly as the router expects. The plaintext exists in one HTTP response, which
  is sent with `Cache-Control: no-store`; it is never logged and never written to `admin_audit`.
- **Sessions.** One signed (HMAC-SHA256), `HttpOnly`, `SameSite=Lax` cookie; `Secure` and the `__Host-`
  prefix when served over HTTPS. Expiry is enforced on the server.
- **What ends a session.** Signing out writes the session id to `admin_revoked_sessions`. Every signed-in
  request checks that table (the answer is cached for 5 seconds), so a copy of the cookie is refused
  after a restart and by every other process too, at the latest 5 seconds later. A session is also
  tied to the credential it was made with: changing `ADMIN_LOCAL_PASSWORD` or `ADMIN_LOCAL_USER`, setting
  `ADMIN_LOCAL_LOGIN=off`, or changing the OIDC issuer, client or required role ends the sessions made
  before. Rotating `ADMIN_SESSION_SECRET` ends all of them.
- **What does not.** A cookie copied from a session that is never signed out stays valid until the idle
  or absolute limit (`ADMIN_SESSION_HOURS`, at most 72). While the database is unreachable the revocation
  check cannot run; nothing can be read or changed then either, because every page needs the same
  database.
- **CSRF.** Every `POST` needs the session's token, as a hidden field or, from htmx, as the
  `X-CSRF-Token` header. Requests the browser marks `Sec-Fetch-Site: cross-site` are refused outright.
  This is a global dependency, not a per-route decorator, so a new route cannot forget it.
- **Headers.** `Content-Security-Policy` with `default-src 'none'` and no `unsafe-inline`: there are no
  inline scripts, styles or event handlers anywhere, and a test fails if one appears. Also
  `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: same-origin`, and HSTS over HTTPS.
- **Audit.** Key changes and their audit row are written in one transaction. The actor is
  `local:<user>` or `oidc:<username>`, so the local account and an OIDC user of the same name stay
  apart; rows of OIDC users also carry the provider's `subject`. Sign-ins are logged best effort, so the
  local account still works while the database is down.
- **Input limits.** A key's expiry can be at most 10 years ahead. Request bodies over 64 KiB, and
  chunked ones, are refused before they are read. Database connections run in UTC whatever the server's
  time zone is.
- **Single process.** The login brakes and the duplicate-submission guard live in memory. Run one
  process (the default); with several workers each would keep its own counters. Signed-out sessions are
  in Postgres and do not depend on this.

## Deployment behind a proxy

The console speaks plain HTTP and expects a TLS-terminating proxy in front.

- **Bind to loopback.** With host networking (the Quadlet setup of this repository, or
  `podman run --network host`) set `ADMIN_HOST=127.0.0.1`, so that only the proxy on the same host can
  reach the console. The image's default `ADMIN_HOST=0.0.0.0` is for a private container network only.
  This matters because proxy headers are trusted by default: anyone who can reach the port directly can
  send their own `X-Forwarded-For` and `X-Forwarded-Proto`, which lets them pick the address the login
  lock is keyed on. If a direct path cannot be ruled out, set `ADMIN_TRUST_PROXY_HEADERS=off`.
- **Tell it its address.** Set `ADMIN_PUBLIC_URL=https://…`. Cookies are then always `Secure`, HSTS is
  sent, and the OIDC redirect URI does not depend on request headers. Without it the console relies on
  `X-Forwarded-Proto` per request, and it logs a warning at start when it listens on a non-loopback
  address without an https `ADMIN_PUBLIC_URL`.
- **What the proxy must send.** `X-Forwarded-Proto` (set, not appended) and the client address as the
  last `X-Forwarded-For` entry, which is what nginx's `$proxy_add_x_forwarded_for` and HAProxy's
  `option forwardfor` do.
- **Redirects** issued by the console are relative, so they stay on the scheme and host the browser used.
- Cookies are per host, not per port: two consoles on one host name share the cookie jar.
- **The router's `/health` and `/v1/models` are public**, so the console needs no router credential.
  What they return is treated as untrusted and escaped.

## Where the numbers come from

`usage_daily` is the complete history: one count per forwarded request, by key and route. `usage_hourly`
is newer and adds the model id, the outcome, token counts and durations, and it also records requests
the router refused without forwarding.

- Requests and errors come from `usage_daily`. Tokens, durations, outcomes and "refused" come from
  `usage_hourly`.
- Breakdowns that only exist hourly (by model, by outcome, the 24 h chart) derive requests and errors
  from `usage_hourly` with the daily table's definitions: refused requests are not requests, and `ok`
  and `client_closed` are not errors.
- With `usage_hourly` empty, token figures show a dash and say why, the 24 h view falls back to today and
  yesterday from the daily totals, and by-model and by-outcome views explain what is missing. Where the
  hourly history starts inside the chosen period, the page says since when tokens are counted.
- All days and hours are UTC.

Chart colours follow the entity: the busiest seven routes (or keys, owners, models) of the last 90 days
keep their colour on every page and under every filter; the rest fold into "Other".

## Tests

```bash
uv run pytest
```

The tests need a real Postgres. With `TEST_DATABASE_URL` set they use that server; otherwise they start
`docker.io/library/postgres:17-alpine` with rootless podman on a free port and remove it afterwards
(`--pull=never`, so the image must be present). Either way they work in schemas created for the run and
dropped at the end, with the files from `../migrations/` applied in order, so existing data is not
touched.

`tests/test_hardening.py` holds one regression test per finding of the security review. The rest
cover: sign-in required on every route; the local account and its throttle; the OIDC flow against a
fake provider (`tests/fakes.py`) including wrong audience, foreign `azp`, expired, wrong nonce, wrong
issuer, bad signature, `alg: none`, HMAC key confusion and a missing role; CSRF; key create, edit,
disable and delete with their audit rows; token format and hash; usage with and without hourly rows; CSV
export; both router health shapes and an unreachable router; the schema and database-down pages.

## Container

```bash
podman build -t mo-admin .
podman run --rm -p 127.0.0.1:8600:8600 --env-file .env mo-admin
```

`python:3.13-slim`, dependencies from `uv.lock` (`uv sync --frozen`), an unprivileged user, and a
healthcheck on `/livez`, which touches neither the database nor the router. Podman only keeps the
`HEALTHCHECK` with `--format docker`; with Quadlet use `HealthCmd` instead.

## Layout

```
mo_admin/
  main.py            application factory, session and header middleware, error pages
  config.py          ADMIN_* settings
  sessions.py        signed cookie sessions      security.py   proxy headers, throttle, CSP
  oidc.py            OpenID Connect client       db.py         pool and schema check
  keys.py            key CRUD and audit rows     usage.py      usage queries
  router_client.py   /health and /v1/models      charts.py     SVG chart geometry
  views/             one module per area         templates/    Jinja2
  static/            css, js, fonts, vendor/htmx (versions and licences in the README files there)
scripts/seed_demo.py demo data for a development database
tests/               pytest, fake provider and fake router
```

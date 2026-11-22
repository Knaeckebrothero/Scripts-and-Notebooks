# Model router admin website: design record

**Date:** 2026-11-19
**Status:** written after the build, as a record of the decisions. Built and tested next to router version 3 and merged to `main` on 2026-11-19. Not released; running at the pilot site with single sign-on since 2026-11-19. The core design ([2026-11-18-model-router-rework-design.md](2026-11-18-model-router-rework-design.md)) left this as a separate project; it was built in the same session under a general go-ahead to rework the UI, so the choices below were made by the builder, not agreed beforehand.
**Scope:** `model-orchestrator/admin_web/`, the web console that replaces the Streamlit page in `model-orchestrator/admin_ui/`. How to run, configure and deploy it is in [`admin_web/README.md`](../../../model-orchestrator/admin_web/README.md); this file records why it looks the way it does.

---

## 1. Why

The Streamlit page manages keys for both router sites. It has one shared username and password, no audit log, no lockout, and bugs in everyday use. Router version 3 adds data the page cannot show (token counts, outcomes, live route status). More GPU machines and more users are coming, so key management needs per-person sign-in and a record of who changed what.

## 2. Goals, non-goals, anti-goals

**Goals**

1. Per-person sign-in through the existing identity provider (Keycloak, OpenID Connect), limited to holders of one role.
2. A local break-glass account for the day the identity provider is down.
3. Everything the Streamlit page does with keys, plus: usage with tokens per key, route and model; live route status; an audit log of every change and sign-in.
4. Works with the database as the router leaves it; the Streamlit page keeps working next to it.

**Non-goals**

- Self-service keys for non-admins. Who signs in was never decided; the website assumes administrators only.
- An admin API in the router. The website reads and writes the router's Postgres directly (see section 4).
- Editing routes. Routes live in the router's YAML file.
- Token quotas.

**Anti-goals**

- A plaintext key stored or logged anywhere.
- A page that works without the required role, or a state-changing request that works without a CSRF token.
- A second build toolchain (Node) for one small internal site.

## 3. Constraints

- Public repository: no internal addresses, account names or credentials in code, tests or docs.
- Key format and hashing are fixed by the router (`sk-mo-` + 40 hex, unsalted SHA-256, first 12 characters as the prefix).
- Postgres 16 and 17. Schema changes go through the router's numbered migrations, additive only.
- One process behind a TLS-terminating proxy, on the same host or cluster as the router.

## 4. Approaches considered

| Approach | Trade-off | Verdict |
|---|---|---|
| Fix the Streamlit page and put an authenticating proxy in front | Least new code. But Streamlit reruns the whole script on every click, has no real routing or forms, and per-person identity would not reach the audit log | Rejected |
| Single-page app (React or similar) on an admin API in the router | Rich client. Needs a Node build, a second artefact to pin and update, and an admin API that does not exist yet | Rejected for now |
| **Server-rendered FastAPI + Jinja2, htmx for partial updates, inline SVG charts** | Same language and stack as the router, one container, no build step, no CDN. Interactivity limited to what htmx swaps give | **Chosen** |

**Direct database access instead of an admin API.** The core design planned an admin API in the router as the website's backend. The website instead uses the router's Postgres with its own connection, because the router's tables are already the interface the Streamlit page uses, and an API would have meant changing the router again during its own rework. Cost: the website needs database credentials, and a schema change has to consider both. The admin API stays a follow-up in `roadmap.md`.

## 5. Design

- **Pages:** Overview (requests, errors, tokens, active keys; route status; busiest keys; keys expired, expiring or never used), Keys (search, sort, create with the secret shown once, edit, enable or disable, delete with typed confirmation), Key detail, Usage (any range, grouped by key, owner, route, model or outcome, CSV export), Models (routes and their ids with live status), Audit.
- **Data:** `usage_daily` for requests and errors (complete history), `usage_hourly` for tokens, durations, outcomes and model ids (from version 3 on). The page says when hourly data is missing or starts inside the chosen range. All times UTC.
- **Router status:** the router's public `GET /health` and `GET /v1/models`, so the website needs no router credential. Their content is treated as untrusted.
- **Sign-in:** authorization code with PKCE (S256); the ID token is verified in full (signature, issuer, audience, `azp`, expiry, nonce; asymmetric algorithms only). The role is looked up in the ID token, then the access token, then userinfo, because Keycloak puts realm roles in the access token by default.
- **A provider on the same host (added 2026-11-22):** `ADMIN_OIDC_INTERNAL_URL` is the issuer as the website itself reaches it. The website's own requests (discovery, keys, token, userinfo) to addresses under the issuer go there; browsers are still sent to the issuer, and the discovery document and the tokens must still name it. It exists for a site whose TLS certificate browsers accept after a warning but programs reject: the website then talks to Keycloak over loopback instead of verifying that certificate. Considered and dropped: switching verification off for the provider (it would also apply when the provider is somewhere else), and letting Keycloak derive its addresses from each request (tokens would then name whichever address the caller used).
- **Break-glass account:** password of at least 12 characters or the website does not start; per-address lockout that doubles up to 15 minutes, plus a global slow-down that never refuses the right password. It can be switched off.
- **Sessions:** one signed cookie, `HttpOnly`, `SameSite=Lax`, `Secure` and `__Host-` over HTTPS, idle and absolute expiry enforced on the server. Sign-out is recorded in `admin_revoked_sessions`, so it holds across restarts and processes. A session is bound to the credential and the OIDC settings it was made with.
- **Request safety:** CSRF token on every `POST` as a global dependency; cross-site requests refused; a strict Content-Security-Policy with no inline code anywhere (a test enforces it); bodies over 64 KiB and chunked bodies refused before they are read.
- **Audit:** each key change and its audit row in one transaction; actors are `local:<user>` or `oidc:<username>` with the provider's subject.
- **Schema:** migrations `0002` (nullable `owner_email`, `created_by`, `notes` on `api_keys`), `0004` (`admin_audit`) and `0005` (`admin_revoked_sessions`), applied by the router at start. The website checks the schema and shows a page saying what is missing instead of failing.
- **Packaging:** own `pyproject.toml` and `uv.lock`, image `model-orchestrator-admin-web` with its own release tag (`model-orchestrator-admin-web-vX.Y.Z`); `latest` moves only on that tag.

## 6. Testing

219 tests against a real Postgres: sign-in required on every route, the local account and its brakes, the OIDC flow against a fake provider (wrong audience, foreign `azp`, expired, wrong nonce, wrong issuer, bad signature, `alg: none`, HMAC key confusion, missing role), CSRF, key lifecycle with audit rows, usage with and without hourly rows, CSV export, both router health shapes and an unreachable router. An independent security review found 8 issues (among them a lockout that could shut out the break-glass account, sign-out that did not survive a restart, and a far-future expiry that broke pages on a database not running in UTC); each is fixed and has a regression test in `tests/test_hardening.py`. The pages were also reviewed in a browser against seeded demo data.

## 7. Not verified, and left open

- ~~Sign-in against a real Keycloak~~: verified 2026-11-19 at the pilot site (a confidential client with PKCE S256, the required role as a realm role read from `realm_access.roles`, no extra mapper).
- ~~A Keycloak next to the router on a host with a certificate programs cannot verify~~: tried 2026-11-22 on a local rig (Keycloak 26.2.5 with the settings of `deployment/keycloak.container`, nginx with `nginx/keycloak.conf.template` and a certificate without a subjectAltName). Without the internal address the sign-in fails at discovery with a certificate error; with it the whole flow works in a browser, an account without the role gets the 403 page, and Keycloak's administration console works through nginx for the allowed addresses. Not yet done at a real site.
- Who may sign in beyond administrators. For the second site, one Keycloak shared with other services there is planned.
- Several workers: the login brakes and the duplicate-submission guard are in memory, so the website runs as one process.
- Retiring the Streamlit page: steps are in `roadmap.md`.

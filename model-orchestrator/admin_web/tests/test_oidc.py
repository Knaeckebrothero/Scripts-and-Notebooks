"""The OpenID Connect flow against the fake provider in ``fakes.py``."""
from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.testclient import TestClient

from mo_admin.config import ConfigError, Settings, load_settings
from mo_admin.oidc import OIDCClient, OIDCError, as_role_set, claim_at, pkce_challenge

from .fakes import FakeProvider, free_port
from .helpers import audit_rows, post

ROLE = "router-admin"


@pytest.fixture()
def sso(provider: FakeProvider, make_client, db) -> TestClient:
    return make_client(
        local_login=False,
        oidc_issuer=provider.issuer,
        oidc_client_id=provider.client_id,
        oidc_client_secret=provider.client_secret,
        oidc_required_role=ROLE,
    )


def begin(client: TestClient, next_url: str | None = None) -> str:
    """Start the flow; returns the provider's authorization URL."""
    response = client.get("/auth/oidc/start" + (f"?next={next_url}" if next_url else ""))
    assert response.status_code == 303, response.text
    return response.headers["location"]


def at_provider(authorization_url: str) -> str:
    """Play the browser at the provider; returns the callback path and query."""
    response = httpx.get(authorization_url, follow_redirects=False)
    assert response.status_code == 302, response.text
    target = urlsplit(response.headers["location"])
    assert target.netloc == "testserver" and target.path == "/auth/oidc/callback"
    return f"{target.path}?{target.query}"


def complete(client: TestClient, next_url: str | None = None):
    return client.get(at_provider(begin(client, next_url)))


def test_sign_in_with_the_provider(sso: TestClient, provider: FakeProvider, db):
    page = sso.get("/login")
    assert 'href="/auth/oidc/start"' in page.text and 'action="/login"' not in page.text

    url = begin(sso)
    query = parse_qs(urlsplit(url).query)
    assert url.startswith(provider.issuer + "/authorize?")
    assert query["response_type"] == ["code"] and query["client_id"] == [provider.client_id]
    assert query["code_challenge_method"] == ["S256"] and len(query["code_challenge"][0]) == 43
    assert query["redirect_uri"] == ["http://testserver/auth/oidc/callback"]
    assert "openid" in query["scope"][0].split()
    assert len(query["state"][0]) >= 24 and len(query["nonce"][0]) >= 24

    response = sso.get(at_provider(url))
    assert response.status_code == 303 and response.headers["location"] == "/"
    home = sso.get("/")
    assert home.status_code == 200
    assert "Mira Castellan" in home.text and "mira.castellan" in home.text

    sent = provider.token_requests[-1]
    assert pkce_challenge(sent["code_verifier"]) == query["code_challenge"][0], "PKCE verifier matches the challenge"
    assert sent["client_secret"] == provider.client_secret
    assert [(r["actor"], r["action"]) for r in audit_rows(db)] == [("oidc:mira.castellan", "auth.login")]
    assert audit_rows(db)[0]["detail"] == {"method": "oidc", "subject": "user-1001"}


def test_the_flow_cookie_is_short_lived_and_cleared(sso: TestClient, provider: FakeProvider):
    start = sso.get("/auth/oidc/start")
    cookie = start.headers["set-cookie"]
    assert cookie.startswith("mo_admin_oidc=") and "HttpOnly" in cookie and "Max-Age=600" in cookie
    assert "Path=/auth/oidc" in cookie and "SameSite=lax" in cookie
    done = sso.get(at_provider(start.headers["location"]))
    assert any(c.startswith('mo_admin_oidc="";') or c.startswith("mo_admin_oidc=;") for c in done.headers.get_list("set-cookie"))


def test_next_survives_the_round_trip(sso: TestClient):
    assert complete(sso, "/keys?status=expired").headers["location"] == "/keys?status=expired"


def test_next_cannot_leave_the_site(sso: TestClient):
    assert complete(sso, "//evil.example/").headers["location"] == "/"


@pytest.mark.parametrize("where", ["access_token", "userinfo"])
def test_roles_are_found_outside_the_id_token(sso: TestClient, provider: FakeProvider, where: str):
    """Keycloak sends realm roles in the access token unless told otherwise."""
    provider.roles_in = where
    assert complete(sso).status_code == 303
    assert sso.get("/").status_code == 200


def test_basic_client_authentication_is_used_when_post_is_not_offered(sso: TestClient, provider: FakeProvider):
    provider.auth_methods = ["client_secret_basic"]
    assert complete(sso).status_code == 303
    assert "client_secret" not in provider.token_requests[-1]


def test_missing_role_gets_a_clear_403(sso: TestClient, provider: FakeProvider, db):
    provider.roles = ["viewer"]
    response = complete(sso)
    assert response.status_code == 403
    assert "This account has no access" in response.text
    assert "mira.castellan" in response.text and ROLE in response.text
    assert sso.get("/").status_code == 303, "no session was created"
    assert [(r["actor"], r["action"]) for r in audit_rows(db)] == [("oidc:mira.castellan", "auth.denied")]
    assert audit_rows(db)[0]["detail"] == {
        "method": "oidc", "reason": "missing role", "required_role": ROLE, "subject": "user-1001",
    }


def test_no_roles_anywhere_is_also_a_403(sso: TestClient, provider: FakeProvider):
    provider.roles_in = "nowhere"
    response = complete(sso)
    assert response.status_code == 403
    assert "sent no roles at" in response.text and "realm_access.roles" in response.text


def test_a_custom_claim_path(provider: FakeProvider, make_client, db):
    client = make_client(
        local_login=False, oidc_issuer=provider.issuer, oidc_client_id=provider.client_id,
        oidc_client_secret=provider.client_secret, oidc_required_role=ROLE, oidc_role_claim="groups",
    )
    assert complete(client).status_code == 403, "roles are under realm_access.roles, not groups"
    provider.user["groups"] = ["staff", ROLE]
    assert complete(client).status_code == 303


@pytest.mark.parametrize(
    "fault",
    ["wrong_audience", "foreign_azp", "expired", "wrong_nonce", "wrong_issuer", "bad_signature", "alg_none",
     "hs256_with_public_key"],
)
def test_invalid_id_tokens_are_rejected(sso: TestClient, provider: FakeProvider, db, fault: str):
    provider.fault = fault
    response = complete(sso)
    assert response.status_code == 400
    assert "could not be verified" in response.text
    assert sso.get("/").status_code == 303, f"{fault}: no session may exist"
    assert audit_rows(db) == []


def test_state_must_match(sso: TestClient, provider: FakeProvider):
    callback = at_provider(begin(sso))
    path, _, query = callback.partition("?")
    forged = parse_qs(query)
    response = sso.get(f"{path}?code={forged['code'][0]}&state=forged-state")
    assert response.status_code == 400
    assert "did not match" in response.text
    assert sso.get("/").status_code == 303


def test_callback_without_the_flow_cookie_is_refused(sso: TestClient, provider: FakeProvider, make_client):
    callback = at_provider(begin(sso))
    other_browser = make_client(
        local_login=False, oidc_issuer=provider.issuer, oidc_client_id=provider.client_id,
        oidc_client_secret=provider.client_secret, oidc_required_role=ROLE,
    )
    response = other_browser.get(callback)
    assert response.status_code == 400 and "Start again" in response.text


def test_a_callback_cannot_be_replayed(sso: TestClient):
    callback = at_provider(begin(sso))
    assert sso.get(callback).status_code == 303
    post(sso, "/logout")
    assert sso.get(callback).status_code == 400, "the flow cookie is gone and the code is spent"


def test_provider_error_is_reported(sso: TestClient):
    url = begin(sso)
    state = parse_qs(urlsplit(url).query)["state"][0]
    response = sso.get(f"/auth/oidc/callback?error=access_denied&state={state}")
    assert response.status_code == 400 and "cancelled at the identity provider" in response.text


def test_wrong_client_secret_fails_cleanly(provider: FakeProvider, make_client, db):
    client = make_client(
        local_login=False, oidc_issuer=provider.issuer, oidc_client_id=provider.client_id,
        oidc_client_secret="the-wrong-secret", oidc_required_role=ROLE,
    )
    response = complete(client)
    assert response.status_code == 400 and "rejected the sign-in" in response.text
    assert "the-wrong-secret" not in response.text


def test_unreachable_provider_keeps_the_login_page_usable(make_client, db):
    client = make_client(
        oidc_issuer=f"http://127.0.0.1:{free_port()}", oidc_client_id="mo-admin", oidc_required_role=ROLE,
    )
    assert client.get("/login").status_code == 200, "the page itself never contacts the provider"
    response = client.get("/auth/oidc/start")
    assert response.status_code == 502
    assert "cannot be reached" in response.text and 'action="/login"' in response.text


def test_discovery_must_be_for_the_configured_issuer(provider: FakeProvider, make_client, db):
    real = provider.issuer
    client = make_client(
        local_login=False, oidc_issuer=real, oidc_client_id=provider.client_id, oidc_required_role=ROLE,
    )
    provider.issuer = real + "/someone-else"
    response = client.get("/auth/oidc/start")
    assert response.status_code == 502 and "misconfigured" in response.text


def test_sign_out_goes_through_the_provider(sso: TestClient, provider: FakeProvider):
    complete(sso)
    response = post(sso, "/logout")
    assert response.status_code == 303
    target = urlsplit(response.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == provider.issuer + "/logout"
    query = parse_qs(target.query)
    assert query["client_id"] == [provider.client_id]
    assert query["post_logout_redirect_uri"] == ["http://testserver/login?signed_out=1"]
    assert sso.get("/").status_code == 303
    assert provider.issuer in sso.get("/login").headers["content-security-policy"].split("form-action")[1]


def test_oidc_routes_are_absent_without_configuration(client: TestClient):
    assert client.get("/auth/oidc/start").status_code == 404
    assert client.get("/auth/oidc/callback?code=x&state=y").status_code == 404
    assert "/auth/oidc/start" not in client.get("/login").text


def test_claim_helpers():
    claims = {"realm_access": {"roles": ["a", "b"]}, "scope": "x y", "resource_access": {"c": {"roles": ["z"]}}}
    assert as_role_set(claim_at(claims, "realm_access.roles")) == {"a", "b"}
    assert as_role_set(claim_at(claims, "resource_access.c.roles")) == {"z"}
    assert as_role_set(claim_at(claims, "scope")) == {"x", "y"}
    assert claim_at(claims, "realm_access.missing") is None
    assert claim_at(claims, "scope.deeper") is None
    assert as_role_set(None) is None


# ------------------------------------------------------ an internal address
# The browser and the website do not always reach the provider at the same
# address: behind a proxy whose certificate the website cannot verify, the
# website talks to the provider directly (ADMIN_OIDC_INTERNAL_URL) while the
# browser keeps using the public address.
PUBLIC = "https://sso.example.test/realms/uni"


@pytest.fixture()
def internal(provider: FakeProvider) -> str:
    """The provider states a public address that cannot be reached from the
    tests. Returns the address it really listens on."""
    listening = provider.issuer
    provider.issuer = PUBLIC
    return listening


@pytest.fixture()
def sso_internal(provider: FakeProvider, internal: str, make_client, db) -> TestClient:
    return make_client(
        local_login=False, oidc_issuer=PUBLIC, oidc_internal_url=internal,
        oidc_client_id=provider.client_id, oidc_client_secret=provider.client_secret, oidc_required_role=ROLE,
    )


def browser_at(internal: str, authorization_url: str) -> str:
    """A browser reaches the public address; the test reaches the fake where it listens."""
    assert authorization_url.startswith(PUBLIC + "/authorize?"), "the browser is sent to the public address"
    return at_provider(internal + authorization_url[len(PUBLIC):])


def test_sign_in_when_the_website_reaches_the_provider_at_an_internal_address(
    sso_internal: TestClient, provider: FakeProvider, internal: str
):
    response = sso_internal.get(browser_at(internal, begin(sso_internal)))
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert "Mira Castellan" in sso_internal.get("/").text
    assert provider.seen == ["discovery", "jwks"] and len(provider.token_requests) == 1


def test_userinfo_is_asked_at_the_internal_address(sso_internal: TestClient, provider: FakeProvider, internal: str):
    provider.roles_in = "userinfo"
    assert sso_internal.get(browser_at(internal, begin(sso_internal))).status_code == 303
    assert sso_internal.get("/").status_code == 200


def test_sign_out_sends_the_browser_to_the_public_address(
    sso_internal: TestClient, provider: FakeProvider, internal: str
):
    sso_internal.get(browser_at(internal, begin(sso_internal)))
    response = post(sso_internal, "/logout")
    assert response.status_code == 303 and response.headers["location"].startswith(PUBLIC + "/logout?")
    policy = sso_internal.get("/login").headers["content-security-policy"]
    assert "https://sso.example.test" in policy and internal not in policy


def test_tokens_must_still_name_the_public_issuer(sso_internal: TestClient, provider: FakeProvider, internal: str):
    provider.fault = "wrong_issuer"
    response = sso_internal.get(browser_at(internal, begin(sso_internal)))
    assert response.status_code == 400 and "could not be verified" in response.text


def test_the_internal_address_must_serve_the_configured_issuer(provider: FakeProvider, internal: str, make_client, db):
    client = make_client(
        local_login=False, oidc_issuer="https://sso.example.test/realms/another", oidc_internal_url=internal,
        oidc_client_id=provider.client_id, oidc_required_role=ROLE,
    )
    response = client.get("/auth/oidc/start")
    assert response.status_code == 502 and "misconfigured" in response.text


@pytest.mark.parametrize(
    "token_endpoint",
    ["https://tokens.example.test/token", PUBLIC + "-other/protocol/token"],
    ids=["another host", "a longer realm name"],
)
def test_only_addresses_under_the_issuer_go_to_the_internal_address(token_endpoint: str):
    """An endpoint the provider keeps somewhere else is used as stated."""
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={
                "issuer": PUBLIC, "authorization_endpoint": PUBLIC + "/auth",
                "token_endpoint": token_endpoint, "jwks_uri": PUBLIC + "/certs",
            })
        return httpx.Response(400, json={"error": "invalid_grant"})

    settings = Settings(
        database_url="postgresql://unused", oidc_issuer=PUBLIC, oidc_internal_url="http://127.0.0.1:8601/realms/uni",
        oidc_client_id="mo-admin", oidc_required_role=ROLE,
    )

    async def redeem() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
            client = OIDCClient(settings, http)
            await client.redeem("a-code", client.new_transaction("/"), "https://admin.example.test/auth/oidc/callback")

    with pytest.raises(OIDCError):
        asyncio.run(redeem())
    assert asked == ["http://127.0.0.1:8601/realms/uni/.well-known/openid-configuration", token_endpoint]


OIDC_ENV = {
    "ADMIN_DATABASE_URL": "postgresql://u:p@localhost/db",
    "ADMIN_SESSION_SECRET": "s" * 40,
    "ADMIN_OIDC_ISSUER": PUBLIC,
    "ADMIN_OIDC_CLIENT_ID": "mo-admin",
    "ADMIN_OIDC_REQUIRED_ROLE": ROLE,
}


def test_the_internal_address_is_read_from_the_environment():
    assert load_settings(OIDC_ENV).oidc_internal_url is None
    settings = load_settings({**OIDC_ENV, "ADMIN_OIDC_INTERNAL_URL": "http://127.0.0.1:8601/realms/uni/"})
    assert settings.oidc_internal_url == "http://127.0.0.1:8601/realms/uni"
    assert settings.oidc_issuer == PUBLIC


def test_the_internal_address_needs_an_issuer():
    env = {k: v for k, v in OIDC_ENV.items() if not k.startswith("ADMIN_OIDC_")}
    with pytest.raises(ConfigError, match="ADMIN_OIDC_ISSUER"):
        load_settings({**env, "ADMIN_OIDC_INTERNAL_URL": "http://127.0.0.1:8601/realms/uni"})


def test_the_internal_address_is_plain_http_on_this_machine_only():
    with pytest.raises(ConfigError, match="ADMIN_OIDC_INTERNAL_URL must use https"):
        load_settings({**OIDC_ENV, "ADMIN_OIDC_INTERNAL_URL": "http://keycloak.internal:8080/realms/uni"})
    assert load_settings({**OIDC_ENV, "ADMIN_OIDC_INTERNAL_URL": "https://keycloak.internal/realms/uni"}).oidc_internal_url

"""Small local stand-ins for the two services the console talks to: an
OpenID provider and the model router. Both are real HTTP servers on a free
localhost port, so the console's own HTTP client is exercised unchanged.

Both can be run by hand, to look at the pages with live status or to try
the single sign-on without a Keycloak::

    uv run python -m tests.fakes router 8612 [routes|backends]
    uv run python -m tests.fakes provider 8613 [role,role]
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import socket
import sys
import threading
import time
from typing import Any
from urllib.parse import urlencode

import jwt
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class Served:
    """A Starlette app running on 127.0.0.1 in a background thread."""

    def __init__(self, app: Starlette, port: int | None = None):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", port or 0))
        self.port = self._socket.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
        self._thread = threading.Thread(target=self._server.run, kwargs={"sockets": [self._socket]}, daemon=True)

    def start(self) -> "Served":
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("fake server did not start")
            time.sleep(0.01)
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)
        self._socket.close()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# --------------------------------------------------------------------- router
HEALTH_ROUTES = {
    "status": "healthy",
    "version": "3.0.0",
    "database": {"status": "healthy"},
    "routes": {"Gemma-4-26B-A4B": "up", "Qwen3-Embedding-8B": "up", "Whisper-large-v3-turbo": "down"},
}
HEALTH_BACKENDS = {
    "status": "degraded",
    "router": "model-orchestrator",
    "database": {"status": "unavailable", "keys_loaded": 12, "seconds_since_key_refresh": 4.2,
                 "usage_queue_depth": 0, "error": "OperationalError: connection refused"},
    "backends": {
        "http://127.0.0.1:8081": "healthy",
        "http://127.0.0.1:8082": "unhealthy",
        "http://127.0.0.1:8083": "unavailable",
        "http://127.0.0.1:8084": "skipped",
    },
}


def model_entry(model_id: str, route: str | None = None, kind: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": model_id, "object": "model", "created": 1704067200, "owned_by": "local"}
    if route:
        entry["x_router"] = {"route": route, "type": kind}
    return entry


class FakeRouter:
    def __init__(self, shape: str = "routes"):
        self.health: Any = json.loads(json.dumps(HEALTH_ROUTES if shape == "routes" else HEALTH_BACKENDS))
        if shape == "routes":
            self.models = [
                model_entry("gemma-4-26b", "Gemma-4-26B-A4B", "chat"),
                model_entry("google/gemma-4-26B-A4B-it", "Gemma-4-26B-A4B", "chat"),
                model_entry("qwen3-embedding-8b", "Qwen3-Embedding-8B", "embedding"),
            ]
        else:
            self.models = [model_entry("gemma-4-26b"), model_entry("qwen3-embedding-8b")]
        self.health_status = 200
        self.models_status = 200
        self.calls: list[str] = []
        self.app = Starlette(routes=[Route("/health", self._health), Route("/v1/models", self._models)])

    async def _health(self, request: Request) -> Response:
        self.calls.append("/health")
        if self.health_status != 200:
            return PlainTextResponse("boom", status_code=self.health_status)
        return JSONResponse(self.health)

    async def _models(self, request: Request) -> Response:
        self.calls.append("/v1/models")
        if self.models_status != 200:
            return PlainTextResponse("boom", status_code=self.models_status)
        return JSONResponse({"object": "list", "data": self.models})


# ----------------------------------------------------------------------- OIDC
class FakeProvider:
    """An OpenID provider with one user and switches for every way a sign-in
    response can be wrong."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.rogue_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "fake-key-1"
        self.client_id = "mo-admin"
        self.client_secret = "fake-client-secret-not-a-real-one"
        self.issuer = ""  # set once the server has a port
        self.issuer_suffix = ""  # "/" for a provider whose issuer ends in a slash
        self.user: dict[str, Any] = {
            "sub": "user-1001",
            "preferred_username": "mira.castellan",
            "name": "Mira Castellan",
            "email": "mira.castellan@example.org",
        }
        self.roles: list[str] = ["router-admin", "offline_access"]
        self.roles_in = "id_token"      # id_token | access_token | userinfo | nowhere
        self.fault: str | None = None   # see _id_token
        self.auth_methods = ["client_secret_post", "client_secret_basic"]
        self.codes: dict[str, dict[str, str]] = {}
        self.token_requests: list[dict[str, str]] = []
        self.seen: list[str] = []
        self.app = Starlette(
            routes=[
                Route("/.well-known/openid-configuration", self._discovery),
                Route("/jwks", self._jwks),
                Route("/authorize", self._authorize),
                Route("/token", self._token, methods=["POST"]),
                Route("/userinfo", self._userinfo),
                Route("/logout", self._logout),
            ]
        )

    @property
    def advertised_issuer(self) -> str:
        """The issuer exactly as discovery and the tokens state it."""
        return self.issuer + self.issuer_suffix

    # -- documents
    async def _discovery(self, request: Request) -> Response:
        self.seen.append("discovery")
        return JSONResponse(
            {
                "issuer": self.advertised_issuer,
                "authorization_endpoint": f"{self.issuer}/authorize",
                "token_endpoint": f"{self.issuer}/token",
                "userinfo_endpoint": f"{self.issuer}/userinfo",
                "jwks_uri": f"{self.issuer}/jwks",
                "end_session_endpoint": f"{self.issuer}/logout",
                "id_token_signing_alg_values_supported": ["RS256"],
                "token_endpoint_auth_methods_supported": self.auth_methods,
                "code_challenge_methods_supported": ["S256"],
            }
        )

    def _jwk(self) -> dict[str, Any]:
        numbers = self.key.public_key().public_numbers()

        def big(value: int) -> str:
            return b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))

        return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": self.kid, "n": big(numbers.n), "e": big(numbers.e)}

    async def _jwks(self, request: Request) -> Response:
        self.seen.append("jwks")
        return JSONResponse({"keys": [self._jwk()]})

    # -- flow
    async def _authorize(self, request: Request) -> Response:
        q = request.query_params
        problems = []
        if q.get("response_type") != "code":
            problems.append("response_type")
        if q.get("client_id") != self.client_id:
            problems.append("client_id")
        if "openid" not in q.get("scope", "").split():
            problems.append("scope")
        if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
            problems.append("pkce")
        if not q.get("state") or not q.get("nonce") or not q.get("redirect_uri"):
            problems.append("state/nonce/redirect_uri")
        if problems:
            return PlainTextResponse("bad authorization request: " + ", ".join(problems), status_code=400)
        code = secrets.token_urlsafe(16)
        self.codes[code] = {
            "challenge": q["code_challenge"],
            "nonce": q["nonce"],
            "redirect_uri": q["redirect_uri"],
        }
        return RedirectResponse(q["redirect_uri"] + "?" + urlencode({"code": code, "state": q["state"]}), status_code=302)

    async def _token(self, request: Request) -> Response:
        form = dict(await request.form())
        self.token_requests.append({k: str(v) for k, v in form.items()})
        client_id, client_secret = form.get("client_id"), form.get("client_secret")
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            client_id, _, client_secret = base64.b64decode(header[6:]).decode().partition(":")
        if client_id != self.client_id or client_secret != self.client_secret:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        grant = self.codes.pop(str(form.get("code")), None)  # one-time use
        if form.get("grant_type") != "authorization_code" or grant is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if form.get("redirect_uri") != grant["redirect_uri"]:
            return JSONResponse({"error": "invalid_grant", "error_description": "redirect_uri"}, status_code=400)
        verifier = str(form.get("code_verifier") or "")
        if b64url(hashlib.sha256(verifier.encode()).digest()) != grant["challenge"]:
            return JSONResponse({"error": "invalid_grant", "error_description": "pkce"}, status_code=400)
        return JSONResponse(
            {
                "token_type": "Bearer",
                "expires_in": 300,
                "access_token": self._access_token(),
                "id_token": self._id_token(grant["nonce"]),
            }
        )

    async def _userinfo(self, request: Request) -> Response:
        if not request.headers.get("authorization", "").startswith("Bearer "):
            return JSONResponse({"error": "invalid_token"}, status_code=401)
        info = dict(self.user)
        if self.roles_in == "userinfo":
            info["realm_access"] = {"roles": self.roles}
        return JSONResponse(info)

    async def _logout(self, request: Request) -> Response:
        return PlainTextResponse("signed out at the provider")

    # -- tokens
    def _sign(self, claims: dict[str, Any], key: Any = None) -> str:
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": self.kid})

    def _access_token(self) -> str:
        now = int(time.time())
        claims = {
            "iss": self.advertised_issuer, "sub": self.user["sub"], "aud": "account", "azp": self.client_id,
            "iat": now, "exp": now + 300, "typ": "Bearer",
        }
        if self.roles_in == "access_token":
            claims["realm_access"] = {"roles": self.roles}
        return self._sign(claims)

    def _id_token(self, nonce: str) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.advertised_issuer, "aud": self.client_id, "iat": now, "exp": now + 300, "nonce": nonce,
            **self.user,
        }
        if self.roles_in == "id_token":
            claims["realm_access"] = {"roles": self.roles}

        fault = self.fault
        if fault == "wrong_audience":
            claims["aud"] = "some-other-client"
        elif fault == "foreign_azp":
            claims["aud"] = [self.client_id, "some-other-client"]
            claims["azp"] = "some-other-client"
        elif fault == "expired":
            claims["iat"] = now - 7200
            claims["exp"] = now - 3600
        elif fault == "wrong_nonce":
            claims["nonce"] = "not-the-nonce-you-sent"
        elif fault == "unicode_nonce":
            claims["nonce"] = "nonçe-é"
        elif fault == "wrong_issuer":
            claims["iss"] = self.issuer + "/another-realm"
        elif fault == "bad_signature":
            return self._sign(claims, self.rogue_key)
        elif fault == "alg_none":
            head = b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
            return f"{head}.{b64url(json.dumps(claims).encode())}."
        elif fault == "hs256_with_public_key":
            # Key confusion: an HMAC keyed with the provider's public key.
            pem = self.key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            head = b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": self.kid}).encode())
            body = b64url(json.dumps(claims).encode())
            signature = hmac.new(pem, f"{head}.{body}".encode(), hashlib.sha256).digest()
            return f"{head}.{body}.{b64url(signature)}"
        return self._sign(claims)


def main(argv: list[str]) -> None:
    kind = argv[1] if len(argv) > 1 else "router"
    port = int(argv[2]) if len(argv) > 2 else 8612
    if kind == "provider":
        # A stand-in identity provider for trying the OIDC sign-in by hand.
        provider = FakeProvider()
        provider.issuer = f"http://127.0.0.1:{port}"
        if len(argv) > 3:
            provider.roles = [role for role in argv[3].split(",") if role]
        print(f"issuer {provider.issuer}  client id {provider.client_id}  client secret {provider.client_secret}")
        print(f"user {provider.user['preferred_username']} with roles {provider.roles}")
        uvicorn.run(provider.app, host="127.0.0.1", port=port, log_level="warning")
        return
    if kind != "router":
        sys.exit("usage: python -m tests.fakes router [port] [routes|backends]\n"
                 "       python -m tests.fakes provider [port] [role,role]")
    shape = argv[3] if len(argv) > 3 else "routes"
    fake = FakeRouter(shape)
    if shape == "routes":
        # Match the demo data of scripts/seed_demo.py.
        from scripts.seed_demo import ROUTES

        fake.health["routes"] = {name: "up" for name, *_ in ROUTES}
        fake.health["routes"]["Whisper-large-v3-turbo"] = "down"
        fake.models = [
            model_entry(model_id, name, kind_)
            for name, kind_, ids, *_ in ROUTES
            if name != "Whisper-large-v3-turbo"
            for model_id in ids
        ]
    uvicorn.run(fake.app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(sys.argv)

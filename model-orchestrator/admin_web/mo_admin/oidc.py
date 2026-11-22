"""OpenID Connect sign-in: authorization-code flow with PKCE.

Works against any provider that publishes a discovery document; the target
is Keycloak. The ID token is validated in full: signature against the
provider's JWKS, issuer, audience (and ``azp`` when present), expiry and the
nonce we sent. Only asymmetric algorithms are accepted, so a token signed
with ``none`` or with an HMAC keyed by the public key is rejected.

The required role is looked up at a configurable claim path, first in the
ID token, then in the access token (Keycloak puts realm roles there by
default; it is verified like the ID token before it is read), then at the
userinfo endpoint.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWK

from .config import Settings
from .security import constant_time_equals

logger = logging.getLogger("mo_admin.oidc")

ALLOWED_ALGORITHMS = (
    "RS256", "RS384", "RS512",
    "PS256", "PS384", "PS512",
    "ES256", "ES384", "ES512",
    "EdDSA",
)
METADATA_TTL = 600.0
JWKS_MIN_REFETCH = 30.0
CLOCK_SKEW = 60


class OIDCError(Exception):
    """Sign-in failed. ``public`` is safe to show; details go to the log."""

    def __init__(self, public: str, detail: str | None = None):
        super().__init__(detail or public)
        self.public = public


@dataclass
class Identity:
    subject: str
    username: str
    name: str | None
    email: str | None
    roles: set[str] = field(default_factory=set)
    role_source: str | None = None


def claim_at(claims: dict[str, Any], path: str) -> Any:
    """Follow a dotted path such as ``realm_access.roles`` into nested claims."""
    node: Any = claims
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def as_role_set(value: Any) -> set[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return {v for v in value.replace(",", " ").split() if v}
    if isinstance(value, (list, tuple, set)):
        return {str(v) for v in value}
    return None


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OIDCClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        assert settings.oidc_issuer and settings.oidc_client_id
        # The configured issuer without a trailing slash: the form used to
        # find the discovery document. What tokens must carry in ``iss`` is
        # ``token_issuer``, taken from that document character for character
        # (some providers' issuers end in a slash, and RFC 8414 wants an
        # exact match between the document and the tokens).
        self.issuer = settings.oidc_issuer.rstrip("/")
        self.token_issuer = self.issuer
        # Where this process reaches the provider, when that is not the
        # address browsers use (ADMIN_OIDC_INTERNAL_URL).
        self._internal = settings.oidc_internal_url.rstrip("/") if settings.oidc_internal_url else None
        self.client_id = settings.oidc_client_id
        self.client_secret = settings.oidc_client_secret
        self.scopes = settings.oidc_scopes
        self.role_claim = settings.oidc_role_claim
        self.username_claim = settings.oidc_username_claim
        self._http = http
        self._metadata: dict[str, Any] | None = None
        self._metadata_at = 0.0
        self._keys: dict[str | None, PyJWK] = {}
        self._keys_at = 0.0

    def _reach(self, url: str) -> str:
        """The address for a request of our own. Anything under the issuer goes
        to the internal address when one is set; an endpoint the provider keeps
        elsewhere is used as stated. Addresses handed to the browser (sign-in
        and sign-out) never pass through here."""
        if self._internal and (url == self.issuer or url.startswith(self.issuer + "/")):
            return self._internal + url[len(self.issuer):]
        return url

    # -- provider documents -------------------------------------------------
    async def metadata(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._metadata is not None and now - self._metadata_at < METADATA_TTL:
            return self._metadata
        url = self._reach(f"{self.issuer}/.well-known/openid-configuration")
        try:
            response = await self._http.get(url)
            response.raise_for_status()
            doc = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError(
                "The identity provider cannot be reached right now.",
                f"discovery failed for {url}: {type(exc).__name__}: {exc}",
            ) from exc
        stated = doc.get("issuer") if isinstance(doc, dict) else None
        if not isinstance(stated, str) or stated.rstrip("/") != self.issuer:
            raise OIDCError(
                "The identity provider is misconfigured.",
                f"discovery issuer {doc.get('issuer') if isinstance(doc, dict) else doc!r} "
                f"does not match ADMIN_OIDC_ISSUER {self.issuer!r}",
            )
        for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not doc.get(name):
                raise OIDCError("The identity provider is misconfigured.", f"discovery lacks {name}")
        self._metadata = doc
        self._metadata_at = now
        self.token_issuer = stated
        return doc

    async def _load_keys(self) -> None:
        meta = await self.metadata()
        try:
            response = await self._http.get(self._reach(meta["jwks_uri"]))
            response.raise_for_status()
            jwks = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError(
                "The identity provider cannot be reached right now.",
                f"JWKS fetch failed: {type(exc).__name__}: {exc}",
            ) from exc
        keys: dict[str | None, PyJWK] = {}
        for raw in jwks.get("keys", []) if isinstance(jwks, dict) else []:
            if raw.get("use") not in (None, "sig"):
                continue
            try:
                key = PyJWK.from_dict(raw)
            except Exception:  # an unsupported key type is not our problem
                continue
            keys[raw.get("kid")] = key
        self._keys = keys
        self._keys_at = time.monotonic()

    async def _signing_key(self, kid: str | None) -> PyJWK:
        if not self._keys:
            await self._load_keys()
        key = self._pick(kid)
        if key is None and time.monotonic() - self._keys_at > JWKS_MIN_REFETCH:
            await self._load_keys()  # the provider may have rotated its keys
            key = self._pick(kid)
        if key is None:
            raise OIDCError("The sign-in response could not be verified.", f"no JWKS key for kid {kid!r}")
        return key

    def _pick(self, kid: str | None) -> PyJWK | None:
        if kid is not None:
            return self._keys.get(kid)
        return next(iter(self._keys.values())) if len(self._keys) == 1 else None

    # -- flow ---------------------------------------------------------------
    @staticmethod
    def new_transaction(next_url: str) -> dict[str, str]:
        return {
            "state": secrets.token_urlsafe(24),
            "nonce": secrets.token_urlsafe(24),
            "verifier": secrets.token_urlsafe(48),
            "next": next_url,
        }

    async def authorization_url(self, tx: dict[str, str], redirect_uri: str) -> str:
        meta = await self.metadata()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "scope": self.scopes,
                "state": tx["state"],
                "nonce": tx["nonce"],
                "code_challenge": pkce_challenge(tx["verifier"]),
                "code_challenge_method": "S256",
            }
        )
        separator = "&" if "?" in meta["authorization_endpoint"] else "?"
        return f"{meta['authorization_endpoint']}{separator}{query}"

    async def end_session_url(self, post_logout_redirect_uri: str) -> str | None:
        try:
            meta = await self.metadata()
        except OIDCError:
            return None
        endpoint = meta.get("end_session_endpoint")
        if not endpoint:
            return None
        query = urlencode({"client_id": self.client_id, "post_logout_redirect_uri": post_logout_redirect_uri})
        return f"{endpoint}{'&' if '?' in endpoint else '?'}{query}"

    async def redeem(self, code: str, tx: dict[str, str], redirect_uri: str) -> Identity:
        meta = await self.metadata()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": tx["verifier"],
            "client_id": self.client_id,
        }
        auth: httpx.BasicAuth | None = None
        if self.client_secret:
            methods = meta.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
            if "client_secret_post" in methods:
                form["client_secret"] = self.client_secret
            else:
                auth = httpx.BasicAuth(self.client_id, self.client_secret)
        try:
            kwargs: dict[str, Any] = {"data": form, "headers": {"Accept": "application/json"}}
            if auth is not None:
                kwargs["auth"] = auth
            response = await self._http.post(self._reach(meta["token_endpoint"]), **kwargs)
        except httpx.HTTPError as exc:
            raise OIDCError(
                "The identity provider cannot be reached right now.",
                f"token request failed: {type(exc).__name__}: {exc}",
            ) from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code != 200 or not isinstance(payload, dict) or "id_token" not in payload:
            error = payload.get("error") if isinstance(payload, dict) else None
            raise OIDCError(
                "The identity provider rejected the sign-in.",
                f"token endpoint answered {response.status_code} error={error!r}",
            )

        claims = await self._verify(payload["id_token"], audience=self.client_id)
        if not constant_time_equals(str(claims.get("nonce", "")), tx["nonce"]):
            raise OIDCError("The sign-in response could not be verified.", "nonce mismatch")
        audiences = claims.get("aud")
        azp = claims.get("azp")
        if isinstance(audiences, list) and len(audiences) > 1 and azp is None:
            raise OIDCError("The sign-in response could not be verified.", "multiple audiences without azp")
        if azp is not None and azp != self.client_id:
            raise OIDCError("The sign-in response could not be verified.", f"azp {azp!r} is not this client")

        roles, source = await self._roles(claims, payload.get("access_token"), meta)
        username = claims.get(self.username_claim) or claims.get("email") or claims["sub"]
        return Identity(
            subject=str(claims["sub"]),
            username=str(username),
            name=claims.get("name"),
            email=claims.get("email"),
            roles=roles,
            role_source=source,
        )

    async def _verify(self, token: str, *, audience: str | None) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise OIDCError("The sign-in response could not be verified.", f"unreadable token: {exc}") from exc
        algorithm = header.get("alg")
        if algorithm not in ALLOWED_ALGORITHMS:
            raise OIDCError(
                "The sign-in response could not be verified.",
                f"token algorithm {algorithm!r} is not accepted",
            )
        key = await self._signing_key(header.get("kid"))
        try:
            return jwt.decode(
                token,
                key=key,
                algorithms=[algorithm],
                issuer=self.token_issuer,
                audience=audience,
                leeway=CLOCK_SKEW,
                options={
                    "require": ["exp", "iat", "iss", "sub"] + (["aud"] if audience else []),
                    "verify_aud": audience is not None,
                },
            )
        except jwt.PyJWTError as exc:
            raise OIDCError(
                "The sign-in response could not be verified.",
                f"token rejected: {type(exc).__name__}: {exc}",
            ) from exc

    async def _roles(
        self, id_claims: dict[str, Any], access_token: Any, meta: dict[str, Any]
    ) -> tuple[set[str], str | None]:
        roles = as_role_set(claim_at(id_claims, self.role_claim))
        if roles is not None:
            return roles, "id_token"

        if isinstance(access_token, str) and access_token.count(".") == 2:
            try:
                access_claims = await self._verify(access_token, audience=None)
            except OIDCError as exc:
                logger.info("access token not usable for roles: %s", exc)
            else:
                azp = access_claims.get("azp")
                same_subject = access_claims.get("sub") == id_claims.get("sub")
                if same_subject and azp in (None, self.client_id):
                    roles = as_role_set(claim_at(access_claims, self.role_claim))
                    if roles is not None:
                        return roles, "access_token"

        endpoint = meta.get("userinfo_endpoint")
        if endpoint and isinstance(access_token, str):
            try:
                response = await self._http.get(
                    self._reach(endpoint), headers={"Authorization": f"Bearer {access_token}"}
                )
                response.raise_for_status()
                info = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.info("userinfo not usable for roles: %s", exc)
            else:
                if isinstance(info, dict) and info.get("sub") == id_claims.get("sub"):
                    roles = as_role_set(claim_at(info, self.role_claim))
                    if roles is not None:
                        return roles, "userinfo"
        return set(), None

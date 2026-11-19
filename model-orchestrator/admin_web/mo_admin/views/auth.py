"""Sign-in and sign-out: local account and OpenID Connect."""
from __future__ import annotations

import hashlib
import logging
import math
from typing import Any

from fastapi import APIRouter, Request
from itsdangerous import BadData, URLSafeTimedSerializer
from starlette.responses import RedirectResponse, Response

from ..deps import AppContext, NotFound, get_ctx, get_session, query_string, render
from ..keys import Actor, write_audit
from ..oidc import OIDCError
from ..security import client_address, constant_time_equals, external_base, is_https, safe_next

logger = logging.getLogger("mo_admin.auth")
router = APIRouter()

OIDC_COOKIE = "mo_admin_oidc"
OIDC_COOKIE_MAX_AGE = 600


def _tx_serializer(ctx: AppContext) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        ctx.settings.session_secret,
        salt="mo-admin.oidc-transaction.v1",
        signer_kwargs={"digest_method": hashlib.sha256, "key_derivation": "hmac"},
    )


async def audit_auth(ctx: AppContext, actor: str, action: str, detail: dict[str, Any]) -> None:
    """Best effort: signing in must work while the database is down.

    ``actor`` is ``local:<user>`` or ``oidc:<username>``, so the local
    account and an OIDC user of the same name stay apart in the log.
    """
    try:
        async with ctx.db.connection() as conn:
            await write_audit(conn, Actor(actor), action, None, detail)
    except Exception as exc:
        logger.warning("could not write audit row for %s by %s: %s", action, actor, type(exc).__name__)


def _login_page(request: Request, *, error: str | None = None, status_code: int = 200, **extra: Any) -> Response:
    ctx = get_ctx(request)
    target = safe_next(request.query_params.get("next"))
    return render(
        request,
        "login.html",
        {
            "error": error,
            "next": target,
            "oidc_href": "/auth/oidc/start" + query_string({"next": target if target != "/" else None}),
            "local_enabled": ctx.settings.local_enabled,
            "oidc_enabled": ctx.settings.oidc_enabled,
            "signed_out": request.query_params.get("signed_out") == "1",
            "typed_user": "",
            **extra,
        },
        status_code=status_code,
    )


@router.get("/login")
async def login_page(request: Request) -> Response:
    if get_session(request).user:
        return RedirectResponse(safe_next(request.query_params.get("next")), status_code=303)
    return _login_page(request)


@router.post("/login")
async def local_login(request: Request) -> Response:
    ctx = get_ctx(request)
    settings = ctx.settings
    if not settings.local_enabled:
        raise NotFound("Local sign-in is switched off.")
    form = await request.form()
    username = str(form.get("username") or "")
    password = str(form.get("password") or "")
    target = safe_next(str(form.get("next") or "") or request.query_params.get("next"))

    # Two brakes, on purpose different in kind:
    #  - a source address that keeps failing is locked out for a while;
    #  - failures from anywhere slow every attempt down, but never refuse
    #    one. A lock shared by all sources could be held shut by anyone, and
    #    this is the account that has to work when all else is down.
    address = client_address(request, settings)
    source = "local:" + address
    wait = ctx.throttle.retry_after(source)
    if wait > 0:
        return _locked(request, wait, username)
    await ctx.slowdown.wait()

    # Both comparisons always run, so timing does not reveal which one failed.
    user_ok = constant_time_equals(username, settings.local_user)
    password_ok = constant_time_equals(password, settings.local_password)
    if not (user_ok and password_ok):
        ctx.slowdown.record_failure()
        locked_for = ctx.throttle.failure(source)
        logger.warning("failed local sign-in from %s", address)
        if locked_for > 0:
            return _locked(request, locked_for, username)
        return _login_page(
            request, error="The user name or password is wrong.", status_code=401, typed_user=username, next=target
        )

    ctx.throttle.success(source)
    session = get_session(request)
    ctx.sessions.sign_in(session, user=settings.local_user, name=None, email=None, method="local")
    await audit_auth(ctx, session.actor or "", "auth.login", {"method": "local"})
    return RedirectResponse(target, status_code=303)


def _locked(request: Request, wait: float, username: str) -> Response:
    seconds = max(1, math.ceil(wait))
    span = f"{seconds} seconds" if seconds < 120 else f"{math.ceil(seconds / 60)} minutes"
    response = _login_page(
        request,
        error=f"Too many failed attempts. Sign-in is paused for {span}.",
        status_code=429,
        typed_user=username,
    )
    response.headers["Retry-After"] = str(seconds)
    return response


# ---------------------------------------------------------------------- OIDC
def _redirect_uri(request: Request, ctx: AppContext) -> str:
    return external_base(request, ctx.settings) + "/auth/oidc/callback"


def _drop_tx_cookie(response: Response, https: bool) -> None:
    response.delete_cookie(OIDC_COOKIE, path="/auth/oidc", httponly=True, secure=https, samesite="lax")


@router.get("/auth/oidc/start")
async def oidc_start(request: Request) -> Response:
    ctx = get_ctx(request)
    if ctx.oidc is None:
        raise NotFound("Single sign-on is not configured.")
    tx = ctx.oidc.new_transaction(safe_next(request.query_params.get("next")))
    try:
        location = await ctx.oidc.authorization_url(tx, _redirect_uri(request, ctx))
    except OIDCError as exc:
        logger.warning("OIDC start failed: %s", exc)
        return _login_page(request, error=exc.public, status_code=502)
    response = RedirectResponse(location, status_code=303)
    response.set_cookie(
        OIDC_COOKIE,
        _tx_serializer(ctx).dumps(tx),
        max_age=OIDC_COOKIE_MAX_AGE,
        path="/auth/oidc",
        httponly=True,
        secure=is_https(request, ctx.settings),
        samesite="lax",
    )
    return response


@router.get("/auth/oidc/callback")
async def oidc_callback(request: Request) -> Response:
    ctx = get_ctx(request)
    if ctx.oidc is None:
        raise NotFound("Single sign-on is not configured.")
    https = is_https(request, ctx.settings)
    params = request.query_params

    def fail(message: str, status_code: int = 400) -> Response:
        response = _login_page(request, error=message, status_code=status_code)
        _drop_tx_cookie(response, https)
        return response

    try:
        tx = _tx_serializer(ctx).loads(request.cookies.get(OIDC_COOKIE) or "", max_age=OIDC_COOKIE_MAX_AGE)
    except BadData:
        return fail("The sign-in took too long or was started in another browser. Start again.")
    if not isinstance(tx, dict) or not constant_time_equals(str(params.get("state", "")), str(tx.get("state", ""))):
        return fail("The sign-in response did not match the request. Start again.")
    if params.get("error"):
        logger.info("OIDC provider returned error=%s", params.get("error"))
        if params.get("error") == "access_denied":
            return fail("Sign-in was cancelled at the identity provider.")
        return fail("The identity provider reported an error. Start again.")
    code = params.get("code")
    if not code:
        return fail("The sign-in response was incomplete. Start again.")

    try:
        identity = await ctx.oidc.redeem(code, tx, _redirect_uri(request, ctx))
    except OIDCError as exc:
        logger.warning("OIDC sign-in failed: %s", exc)
        return fail(exc.public, 502 if "reached" in exc.public else 400)

    actor = f"oidc:{identity.username}"
    required = ctx.settings.oidc_required_role
    if required not in identity.roles:
        logger.warning("OIDC sign-in denied for %s: role %s missing", identity.username, required)
        # Any account of the provider can come here as often as it likes, so
        # the audit log gets one row per account per ten minutes, not one per try.
        if ctx.denials.first(identity.subject):
            await audit_auth(
                ctx, actor, "auth.denied",
                {"method": "oidc", "reason": "missing role", "required_role": required,
                 "subject": identity.subject},
            )
        response = render(
            request,
            "forbidden.html",
            {
                "username": identity.username,
                "required_role": required,
                "role_claim": ctx.settings.oidc_role_claim,
                "roles_found": identity.role_source is not None,
                "logout_url": await ctx.oidc.end_session_url(external_base(request, ctx.settings) + "/login"),
            },
            status_code=403,
        )
        _drop_tx_cookie(response, https)
        return response

    ctx.sessions.sign_in(
        get_session(request), user=identity.username, name=identity.name, email=identity.email, method="oidc",
        subject=identity.subject,
    )
    await audit_auth(ctx, actor, "auth.login", {"method": "oidc", "subject": identity.subject})
    response = RedirectResponse(safe_next(tx.get("next")), status_code=303)
    _drop_tx_cookie(response, https)
    return response


@router.post("/logout")
async def logout(request: Request) -> Response:
    ctx = get_ctx(request)
    session = get_session(request)
    actor, method, subject = session.actor, session.method, session.subject
    if actor and session.sid:
        # Only a signed-in session is worth remembering as revoked. An
        # anonymous one holds nothing, and anyone can produce them endlessly.
        await ctx.revocations.revoke(session.sid, session.expires)
    ctx.sessions.sign_out(session)
    location = "/login?signed_out=1"
    if actor:
        detail = {"method": method, "subject": subject} if method == "oidc" else {"method": method}
        await audit_auth(ctx, actor, "auth.logout", detail)
        if method == "oidc" and ctx.oidc is not None:
            base = external_base(request, ctx.settings)
            location = await ctx.oidc.end_session_url(base + "/login?signed_out=1") or location
    return RedirectResponse(location, status_code=303)


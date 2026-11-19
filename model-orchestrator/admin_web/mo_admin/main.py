"""Application factory: wiring, the session and security-header middleware,
and one place where every failure turns into a readable page."""
from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
import jinja2
import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg_pool import PoolTimeout
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from . import __version__, formatting, usage
from .config import Settings, load_settings
from .db import Database, SchemaNotReady
from .deps import (
    SAFE_METHODS,
    AppContext,
    BadRequest,
    CsrfError,
    LoginRequired,
    NotFound,
    csrf_protect,
    get_ctx,
    is_htmx,
    query_string,
    render,
)
from .oidc import OIDCClient
from .revocations import Revocations
from .router_client import RouterClient
from .security import (
    SECURITY_HEADERS,
    LoginSlowdown,
    LoginThrottle,
    OncePer,
    content_security_policy,
    is_https,
    safe_next,
)
from .sessions import Session, SessionManager, credential_binding
from .views import auth, keys, overview, system
from .views import usage as usage_views

logger = logging.getLogger("mo_admin")

PACKAGE_DIR = Path(__file__).parent
SESSION_COOKIE = "mo_admin_session"
SECURE_SESSION_COOKIE = "__Host-mo_admin_session"


MAX_BODY_BYTES = 64 * 1024
# Failed sign-ins from all sources together start to slow every attempt down
# once there are this many times the per-source limit of them.
SLOWDOWN_FACTOR = 4
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _body_size_ok(request: Request) -> bool:
    # A Transfer-Encoding header means the real size is unknown, and when it
    # comes together with a Content-Length the server follows the encoding
    # and ignores the (possibly tiny) length. Browsers never send forms that
    # way, so both cases are refused.
    if request.headers.get("transfer-encoding"):
        return False
    declared = request.headers.get("content-length")
    if declared is None:
        return True  # no body
    return declared.isascii() and declared.isdigit() and int(declared) <= MAX_BODY_BYTES


def session_bindings(settings: Settings) -> dict[str, str | None]:
    """What a session of each sign-in method is tied to (see sessions.py)."""
    local = oidc = None
    if settings.local_enabled:
        local = credential_binding(settings.session_secret, "local", settings.local_user, settings.local_password)
    if settings.oidc_enabled:
        oidc = credential_binding(
            settings.session_secret, "oidc", (settings.oidc_issuer or "").rstrip("/"),
            settings.oidc_client_id or "", settings.oidc_required_role or "", settings.oidc_role_claim,
        )
    return {"local": local, "oidc": oidc}


def warn_about_plain_cookies(settings: Settings) -> None:
    """Say so at start when the session cookie may travel without Secure."""
    if (settings.public_url or "").startswith("https://") or settings.host in LOOPBACK_HOSTS:
        return
    logger.warning(
        "ADMIN_HOST=%s accepts connections from other machines and ADMIN_PUBLIC_URL is not an https URL: "
        "the session cookie gets the Secure flag only on requests that arrive with X-Forwarded-Proto: https. "
        "Put a TLS proxy in front, set ADMIN_PUBLIC_URL=https://..., and bind ADMIN_HOST=127.0.0.1 when "
        "the proxy runs on the same host.",
        settings.host,
    )


def session_cookie_name(https: bool) -> str:
    return SECURE_SESSION_COOKIE if https else SESSION_COOKIE


def _asset_version() -> str:
    digest = hashlib.sha256()
    for path in sorted((PACKAGE_DIR / "static").rglob("*")):
        if path.is_file() and path.suffix in (".css", ".js", ".svg", ".woff2"):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def build_templates() -> Jinja2Templates:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(PACKAGE_DIR / "templates"),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(formatting.FILTERS)
    env.globals.update(
        qs=query_string,
        outcome_label=usage.outcome_label,
        outcome_class=usage.outcome_class,
        app_version=__version__,
    )
    return Jinja2Templates(env=env)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.database_url, timeout=settings.database_timeout)
        http = httpx.AsyncClient(
            timeout=httpx.Timeout(6.0, connect=3.0),
            follow_redirects=False,
            headers={"User-Agent": f"mo-admin/{__version__}"},
        )
        router = RouterClient(settings.router_url, http)
        app.state.ctx = AppContext(
            settings=settings,
            db=db,
            sessions=SessionManager(
                settings.session_secret,
                lifetime_seconds=settings.session_hours * 3600,
                idle_seconds=settings.session_idle_minutes * 60,
                bindings=session_bindings(settings),
            ),
            revocations=Revocations(db),
            throttle=LoginThrottle(
                max_attempts=settings.login_max_attempts, lockout=settings.login_lockout_seconds
            ),
            slowdown=LoginSlowdown(threshold=settings.login_max_attempts * SLOWDOWN_FACTOR),
            denials=OncePer(600.0),
            http=http,
            router=router,
            oidc=OIDCClient(settings, http) if settings.oidc_enabled else None,
            templates=build_templates(),
            ranking=usage.Ranking(db),
            asset_version=_asset_version(),
        )
        await db.open()
        warn_about_plain_cookies(settings)
        if not settings.oidc_enabled and not settings.local_enabled:
            logger.warning("no sign-in method is configured: set ADMIN_OIDC_* or ADMIN_LOCAL_PASSWORD")
        try:
            yield
        finally:
            await router.aclose()
            await http.aclose()
            await db.close()

    issuer = urlsplit(settings.oidc_issuer) if settings.oidc_issuer else None
    csp = content_security_policy((f"{issuer.scheme}://{issuer.netloc}",) if issuer else ())

    app = FastAPI(
        title=settings.site_name,
        lifespan=lifespan,
        # The framework's own slash redirect builds an absolute URL from the
        # request as this process sees it, which is plain http behind a TLS
        # proxy. The middleware below redirects with a relative Location.
        redirect_slashes=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(csrf_protect)],
    )

    # ------------------------------------------------------------ middleware
    async def session_and_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        ctx = get_ctx(request)
        path = request.url.path
        https = is_https(request, settings)
        static = path.startswith("/static/")

        if static or path == "/livez":
            request.state.session = Session()
            response = await call_next(request)
        elif len(path) > 1 and path.endswith("/"):
            # "/keys/" means "/keys". Relative, so scheme and host stay what
            # the browser used; and only for plain local paths, so that
            # "//elsewhere.example/" can never become a redirect target.
            request.state.session = Session()
            target = path.rstrip("/") + (f"?{request.url.query}" if request.url.query else "")
            if safe_next(target) == target:
                response = RedirectResponse(target, status_code=307)
            else:
                response = render(
                    request, "error.html",
                    {"title": "Not found", "message": "This page does not exist.", "code": 404}, status_code=404,
                )
        elif request.method not in SAFE_METHODS and not _body_size_ok(request):
            # Every form here is a few hundred bytes. Refuse anything large
            # before it is read, signed in or not.
            request.state.session = Session()
            response = PlainTextResponse("Request body too large.", status_code=413)
        else:
            cookie_name = session_cookie_name(https)
            raw = request.cookies.get(cookie_name)
            passive = request.headers.get("x-passive") == "1"
            session = ctx.sessions.load(raw, passive=passive)
            if session.user and session.sid and await ctx.revocations.is_revoked(session.sid):
                session = Session(cleared=True)  # signed out, here or in another process
            request.state.session = session
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("unhandled error on %s %s", request.method, path)
                response = render(
                    request,
                    "error.html",
                    {
                        "title": "Something went wrong",
                        "message": "The console hit an unexpected error. It has been logged; try again.",
                        "code": 500,
                    },
                    status_code=500,
                )
            session = request.state.session
            if session.data and session.dirty:
                response.set_cookie(
                    cookie_name,
                    ctx.sessions.dump(session),
                    max_age=ctx.sessions.max_age(session),
                    path="/",
                    httponly=True,
                    secure=https,
                    samesite="lax",
                )
            elif raw and not session.data:
                response.delete_cookie(cookie_name, path="/", httponly=True, secure=https, samesite="lax")
            response.headers["Cache-Control"] = "no-store"

        response.headers.setdefault("Content-Security-Policy", csp)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if https:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if static:
            versioned = "v" in request.query_params
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable" if versioned else "public, max-age=300"
            )
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=session_and_headers)

    # ------------------------------------------------------------- handlers
    @app.exception_handler(LoginRequired)
    async def on_login_required(request: Request, exc: LoginRequired) -> Response:
        if is_htmx(request):
            current = urlsplit(request.headers.get("hx-current-url", ""))
            target = safe_next(current.path + (f"?{current.query}" if current.query else ""))
            location = "/login" + query_string({"next": target if target != "/" else None})
            return PlainTextResponse("Sign in to continue.", status_code=401, headers={"HX-Redirect": location})
        if request.method == "GET":
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            location = "/login" + (f"?next={quote(target, safe='')}" if target != "/" else "")
            return RedirectResponse(location, status_code=303)
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(CsrfError)
    async def on_csrf(request: Request, exc: CsrfError) -> Response:
        return render(
            request,
            "error.html",
            {
                "title": "This form has expired",
                "message": "The request did not carry a valid security token, so nothing was changed. "
                           "Go back, reload the page and try again.",
                "code": 403,
            },
            status_code=403,
        )

    @app.exception_handler(NotFound)
    async def on_not_found(request: Request, exc: NotFound) -> Response:
        return render(
            request, "error.html",
            {"title": "Not found", "message": exc.what, "code": 404}, status_code=404,
        )

    @app.exception_handler(BadRequest)
    async def on_bad_request(request: Request, exc: BadRequest) -> Response:
        return render(
            request, "error.html",
            {"title": "That request is not valid", "message": exc.what, "code": 400}, status_code=400,
        )

    @app.exception_handler(psycopg.DataError)
    async def on_data_error(request: Request, exc: psycopg.DataError) -> Response:
        # A value Postgres cannot take, such as a NUL byte in a search field.
        logger.info("request refused by the database: %s", type(exc).__name__)
        return render(
            request, "error.html",
            {"title": "That request is not valid",
             "message": "It contains a value the database cannot store or compare. Nothing was changed.",
             "code": 400},
            status_code=400,
        )

    @app.exception_handler(SchemaNotReady)
    async def on_schema(request: Request, exc: SchemaNotReady) -> Response:
        return render(request, "schema.html", {"missing": exc.missing}, status_code=503)

    @app.exception_handler(psycopg.errors.UndefinedTable)
    @app.exception_handler(psycopg.errors.UndefinedColumn)
    async def on_undefined(request: Request, exc: Exception) -> Response:
        ctx = get_ctx(request)
        ctx.db.forget_schema()
        try:
            missing = await ctx.db.missing_schema()
        except Exception:
            missing = []
        return render(request, "schema.html", {"missing": missing}, status_code=503)

    @app.exception_handler(psycopg.OperationalError)
    @app.exception_handler(PoolTimeout)
    async def on_database_down(request: Request, exc: Exception) -> Response:
        logger.warning("database unavailable: %s: %s", type(exc).__name__, str(exc).splitlines()[0] if str(exc) else "")
        return render(request, "database_down.html", {}, status_code=503, headers={"Retry-After": "10"})

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, exc: StarletteHTTPException) -> Response:
        titles = {404: "Not found", 405: "Not allowed here", 429: "Slow down"}
        messages = {
            404: "This page does not exist.",
            405: "This address does not accept that kind of request.",
        }
        return render(
            request,
            "error.html",
            {
                "title": titles.get(exc.status_code, "Request refused"),
                "message": messages.get(exc.status_code, str(exc.detail)),
                "code": exc.status_code,
            },
            status_code=exc.status_code,
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(RequestValidationError)
    async def on_validation(request: Request, exc: RequestValidationError) -> Response:
        return render(
            request, "error.html",
            {"title": "Not found", "message": "This address is not valid.", "code": 404}, status_code=404,
        )

    # --------------------------------------------------------------- routes
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    app.include_router(system.public)
    app.include_router(auth.router)
    app.include_router(overview.router)
    app.include_router(keys.router)
    app.include_router(usage_views.router)
    app.include_router(system.router)
    return app

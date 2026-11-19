"""What every request handler shares: the application context, the guards
(sign-in, CSRF, schema) and template rendering."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from .config import Settings
from .db import Database
from .keys import Actor
from .oidc import OIDCClient
from .revocations import Revocations
from .router_client import RouterClient
from .security import LoginSlowdown, LoginThrottle, OncePer, constant_time_equals, safe_next
from .sessions import Session, SessionManager
from .usage import Ranking

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class LoginRequired(Exception):
    """The request needs a signed-in administrator."""


class CsrfError(Exception):
    """A state-changing request came without a valid CSRF token."""


class NotFound(Exception):
    def __init__(self, what: str = "This page does not exist."):
        super().__init__(what)
        self.what = what


class BadRequest(Exception):
    """The address or form carries a value that cannot be meant."""

    def __init__(self, what: str = "This request is not valid."):
        super().__init__(what)
        self.what = what


@dataclass
class SubmissionGuard:
    """Remembers form submission ids so a browser re-posting the "create key"
    form (reload, back button) does not create a second key."""

    seen: dict[str, None] = field(default_factory=dict)
    limit: int = 2000

    def first_time(self, submission_id: str) -> bool:
        if not submission_id or submission_id in self.seen:
            return False
        self.seen[submission_id] = None
        while len(self.seen) > self.limit:
            self.seen.pop(next(iter(self.seen)))
        return True


@dataclass
class AppContext:
    settings: Settings
    db: Database
    sessions: SessionManager
    revocations: Revocations
    throttle: LoginThrottle    # per source address: locks that source out
    slowdown: LoginSlowdown    # all sources together: delays, never refuses
    denials: OncePer           # refused OIDC sign-ins worth an audit row
    http: httpx.AsyncClient
    router: RouterClient
    oidc: OIDCClient | None
    templates: Jinja2Templates
    ranking: Ranking
    asset_version: str
    submissions: SubmissionGuard = field(default_factory=SubmissionGuard)


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def get_session(request: Request) -> Session:
    return request.state.session


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------- guards
async def csrf_protect(request: Request) -> None:
    """Global dependency: every non-safe request must carry the session's token.

    htmx sends it as the ``X-CSRF-Token`` header (set once on ``<body>``),
    plain forms as the hidden ``csrf_token`` field. A request the browser
    itself labels cross-site is refused before the token is even looked at.
    """
    if request.method in SAFE_METHODS:
        return
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise CsrfError()
    expected = get_session(request).data.get("csrf")
    supplied: Any = request.headers.get("x-csrf-token")
    if supplied is None:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            supplied = (await request.form()).get("csrf_token")
    # Digest comparison: the supplied token is arbitrary text, and
    # hmac.compare_digest refuses non-ASCII strings with an exception.
    if not expected or not isinstance(supplied, str) or not constant_time_equals(expected, supplied):
        raise CsrfError()


async def require_user(request: Request) -> Actor:
    """The signed-in administrator, as the audit log will name them."""
    session = get_session(request)
    actor = session.actor
    if not actor:
        raise LoginRequired()
    return Actor(actor, session.subject if session.method == "oidc" else None)


async def require_db(request: Request) -> Database:
    db = get_ctx(request).db
    await db.require_schema()
    return db


# ---------------------------------------------------------------- rendering
NAV = (
    ("overview", "/", "Overview"),
    ("keys", "/keys", "Keys"),
    ("usage", "/usage", "Usage"),
    ("models", "/models", "Models"),
    ("audit", "/audit", "Audit"),
)


def query_string(params: dict[str, Any], **overrides: Any) -> str:
    """``?a=1&b=2`` from params with overrides; empty and None values drop out."""
    merged = {**params, **overrides}
    pairs = [(k, v) for k, v in merged.items() if v not in (None, "", False)]
    return ("?" + urlencode(pairs)) if pairs else ""


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def render(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> Response:
    ctx = get_ctx(request)
    session: Session = getattr(request.state, "session", None) or Session()
    theme = request.cookies.get("mo_theme")
    base: dict[str, Any] = {
        "settings": ctx.settings,
        "site_name": ctx.settings.site_name,
        "session": session,
        "user": session.user,
        "csrf_token": session.csrf,
        "flashes": session.pop_flashes(),
        "nav": NAV,
        "active": None,
        "now": utcnow(),
        "theme": theme if theme in ("light", "dark") else None,
        "asset_version": ctx.asset_version,
        # Used as a link target and as "return here" in forms: keep it a plain local path.
        "current_url": safe_next(request.url.path + (f"?{request.url.query}" if request.url.query else "")),
    }
    if context:
        base.update(context)
    return ctx.templates.TemplateResponse(request, template, base, status_code=status_code, headers=headers)

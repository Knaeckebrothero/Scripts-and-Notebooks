"""
Streamlit admin UI for the model orchestrator.

Manages API keys and shows usage, reading/writing the same Postgres the
router uses (tables are created by the router on startup — see store.py).
Keys are stored hashed (SHA-256); the plaintext key is displayed exactly
once, right after creation.

Environment:
  DATABASE_URL        postgresql://user:pass@host:port/db   (required)
  UI_ADMIN_USER       login user name (default: admin)
  UI_ADMIN_PASSWORD   login password (required — UI refuses login if unset)
"""
import hashlib
import hmac
import os
import secrets
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd
import psycopg
import streamlit as st
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ADMIN_USER = os.environ.get("UI_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("UI_ADMIN_PASSWORD", "")

KEY_PREFIX_LEN = 12  # chars of the key kept for display, e.g. "sk-mo-3fa9c1"


# ---------------------------------------------------------------- database
def _connect():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def q(sql: str, params=()) -> list:
    """Run a SELECT, return rows as dicts."""
    with _connect() as conn:
        return conn.execute(sql, params).fetchall()


def ex(sql: str, params=()) -> None:
    """Run a write statement (commits on success)."""
    with _connect() as conn:
        conn.execute(sql, params)


def hash_key(token: str) -> str:
    """SHA-256 hex of a full API key. Must match store.hash_key in the router."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- auth gate
def require_login() -> None:
    if st.session_state.get("authenticated"):
        return
    st.title("Model Orchestrator — Admin")
    with st.form("login"):
        user = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        if (
            ADMIN_PASSWORD
            and hmac.compare_digest(user, ADMIN_USER)
            and hmac.compare_digest(password, ADMIN_PASSWORD)
        ):
            st.session_state["authenticated"] = True
            st.rerun()
        if not ADMIN_PASSWORD:
            st.error("UI_ADMIN_PASSWORD is not configured — login disabled.")
        else:
            st.error("Invalid credentials.")
    st.stop()


# ---------------------------------------------------------------- keys tab
def render_keys_tab() -> None:
    routes = q("SELECT name, type FROM routes ORDER BY name")
    route_names = [r["name"] for r in routes]

    # A freshly created key survives the rerun via session_state and is shown
    # until explicitly dismissed — it cannot be recovered afterwards.
    if st.session_state.get("new_key"):
        st.success("Key created — copy it now, it will not be shown again:")
        st.code(st.session_state["new_key"], language=None)
        if st.button("Dismiss", key="dismiss_new_key"):
            del st.session_state["new_key"]
            st.rerun()
        st.divider()

    with st.expander("➕ Create a new API key"):
        with st.form("create_key", clear_on_submit=True):
            name = st.text_input("Key name", placeholder="e.g. RAG pipeline prod")
            owner = st.text_input("Owner", placeholder="e.g. jdoe")
            rpm = st.number_input(
                "Rate limit (requests per minute)", min_value=1, max_value=1_000_000, value=100
            )
            allowed = st.multiselect(
                "Allowed model routes (empty = all models)", options=route_names
            )
            set_expiry = st.checkbox("Set an expiration date")
            expiry = st.date_input("Expires on (end of day, UTC)", value=date.today() + timedelta(days=365))
            if st.form_submit_button("Create key"):
                if not name or not owner:
                    st.error("Name and owner are required.")
                else:
                    token = "sk-mo-" + secrets.token_hex(20)
                    expires_at = (
                        datetime.combine(expiry, time(23, 59, 59), tzinfo=timezone.utc)
                        if set_expiry
                        else None
                    )
                    ex(
                        """
                        INSERT INTO api_keys
                            (key_hash, key_prefix, name, owner, rate_limit_rpm,
                             allowed_models, enabled, expires_at)
                        VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s)
                        """,
                        (
                            hash_key(token),
                            token[:KEY_PREFIX_LEN],
                            name.strip(),
                            owner.strip(),
                            int(rpm),
                            allowed or None,
                            expires_at,
                        ),
                    )
                    st.session_state["new_key"] = token
                    st.rerun()

    keys = q(
        """
        SELECT k.id, k.name, k.owner, k.key_prefix, k.rate_limit_rpm,
               k.allowed_models, k.enabled, k.expires_at, k.created_at,
               k.last_used_at, COALESCE(u.requests_30d, 0) AS requests_30d
        FROM api_keys k
        LEFT JOIN (
            SELECT api_key_id, SUM(request_count) AS requests_30d
            FROM usage_daily
            WHERE day >= CURRENT_DATE - 29
            GROUP BY api_key_id
        ) u ON u.api_key_id = k.id
        ORDER BY k.created_at DESC
        """
    )
    if not keys:
        st.info("No API keys yet — create the first one above.")
        return

    df = pd.DataFrame(keys)
    df["models"] = df["allowed_models"].apply(lambda a: ", ".join(sorted(a)) if a else "all")
    df["expires"] = df["expires_at"].apply(lambda d: d.date().isoformat() if d else "never")
    st.dataframe(
        df[
            ["name", "owner", "key_prefix", "rate_limit_rpm", "models",
             "enabled", "expires", "requests_30d", "created_at", "last_used_at"]
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Manage a key")
    options = {f"{k['name']} — {k['key_prefix']}… ({k['owner']})": k for k in keys}
    selected_label = st.selectbox("Key", options=list(options.keys()))
    key = options[selected_label]
    kid = key["id"]

    col_toggle, col_delete = st.columns(2)
    with col_toggle:
        verb = "Disable" if key["enabled"] else "Enable"
        if st.button(f"{verb} this key", key=f"toggle_{kid}"):
            ex("UPDATE api_keys SET enabled = NOT enabled WHERE id = %s", (kid,))
            st.rerun()
    with col_delete:
        confirm = st.checkbox("Really delete? (usage history is kept)", key=f"confirm_{kid}")
        if st.button("Delete this key", key=f"delete_{kid}", disabled=not confirm):
            ex("DELETE FROM api_keys WHERE id = %s", (kid,))
            st.rerun()

    with st.form(f"edit_{kid}"):
        st.caption("Edit key settings")
        new_name = st.text_input("Name", value=key["name"])
        new_owner = st.text_input("Owner", value=key["owner"])
        new_rpm = st.number_input(
            "Rate limit (requests per minute)",
            min_value=1, max_value=1_000_000, value=int(key["rate_limit_rpm"]),
        )
        current_allowed = sorted(key["allowed_models"]) if key["allowed_models"] else []
        new_allowed = st.multiselect(
            "Allowed model routes (empty = all models)",
            options=sorted(set(route_names) | set(current_allowed)),
            default=current_allowed,
        )
        has_expiry = key["expires_at"] is not None
        new_set_expiry = st.checkbox("Set an expiration date", value=has_expiry)
        new_expiry = st.date_input(
            "Expires on (end of day, UTC)",
            value=key["expires_at"].date() if has_expiry else date.today() + timedelta(days=365),
        )
        if st.form_submit_button("Save changes"):
            expires_at = (
                datetime.combine(new_expiry, time(23, 59, 59), tzinfo=timezone.utc)
                if new_set_expiry
                else None
            )
            ex(
                """
                UPDATE api_keys
                SET name = %s, owner = %s, rate_limit_rpm = %s,
                    allowed_models = %s, expires_at = %s
                WHERE id = %s
                """,
                (new_name.strip(), new_owner.strip(), int(new_rpm),
                 new_allowed or None, expires_at, kid),
            )
            st.rerun()


# ---------------------------------------------------------------- usage tab
def render_usage_tab() -> None:
    col_from, col_to = st.columns(2)
    with col_from:
        day_from = st.date_input("From", value=date.today() - timedelta(days=29))
    with col_to:
        day_to = st.date_input("To", value=date.today())
    if day_from > day_to:
        st.error("Invalid range.")
        return

    totals = q(
        """
        SELECT COALESCE(SUM(request_count), 0) AS requests,
               COALESCE(SUM(error_count), 0) AS errors,
               COUNT(DISTINCT api_key_id) AS active_keys
        FROM usage_daily WHERE day BETWEEN %s AND %s
        """,
        (day_from, day_to),
    )[0]
    m1, m2, m3 = st.columns(3)
    m1.metric("Requests", f"{totals['requests']:,}")
    m2.metric("Errors", f"{totals['errors']:,}")
    m3.metric("Active keys", totals["active_keys"])

    daily = q(
        """
        SELECT day, model, SUM(request_count) AS requests
        FROM usage_daily WHERE day BETWEEN %s AND %s
        GROUP BY day, model ORDER BY day
        """,
        (day_from, day_to),
    )
    if not daily:
        st.info("No usage recorded in this range.")
        return

    pivot = (
        pd.DataFrame(daily)
        .pivot_table(index="day", columns="model", values="requests", fill_value=0)
    )
    st.caption("Requests per day, by model route")
    st.bar_chart(pivot)

    col_owner, col_key = st.columns(2)
    with col_owner:
        st.caption("By owner")
        by_owner = q(
            """
            SELECT owner, SUM(request_count) AS requests, SUM(error_count) AS errors
            FROM usage_daily WHERE day BETWEEN %s AND %s
            GROUP BY owner ORDER BY requests DESC
            """,
            (day_from, day_to),
        )
        st.dataframe(pd.DataFrame(by_owner), use_container_width=True, hide_index=True)
    with col_key:
        st.caption("By key (deleted keys keep their history)")
        by_key = q(
            """
            SELECT COALESCE(k.name, '(deleted)') AS key_name, u.owner,
                   SUM(u.request_count) AS requests, SUM(u.error_count) AS errors
            FROM usage_daily u
            LEFT JOIN api_keys k ON k.id = u.api_key_id
            WHERE u.day BETWEEN %s AND %s
            GROUP BY 1, 2 ORDER BY requests DESC
            """,
            (day_from, day_to),
        )
        st.dataframe(pd.DataFrame(by_key), use_container_width=True, hide_index=True)

    st.caption("By model route")
    by_model = q(
        """
        SELECT model, SUM(request_count) AS requests, SUM(error_count) AS errors
        FROM usage_daily WHERE day BETWEEN %s AND %s
        GROUP BY model ORDER BY requests DESC
        """,
        (day_from, day_to),
    )
    st.dataframe(pd.DataFrame(by_model), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------- main
st.set_page_config(page_title="Model Orchestrator Admin", page_icon="🔑", layout="wide")
require_login()

with st.sidebar:
    st.write(f"Logged in as **{ADMIN_USER}**")
    if st.button("Log out"):
        st.session_state.clear()
        st.rerun()

st.title("Model Orchestrator — Admin")

try:
    q("SELECT 1 FROM api_keys LIMIT 1")
except psycopg.OperationalError as e:
    st.error(f"Cannot reach Postgres: {e}")
    st.stop()
except psycopg.errors.UndefinedTable:
    st.error(
        "Database schema not initialized yet — start the router once "
        "(it creates the tables on startup), then reload this page."
    )
    st.stop()

tab_keys, tab_usage = st.tabs(["🔑 API keys", "📊 Usage"])
with tab_keys:
    render_keys_tab()
with tab_usage:
    render_usage_tab()

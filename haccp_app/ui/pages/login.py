"""
Login page for HACCP application.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

import streamlit as st

from auth import (
    verify_password,
    get_client_ip,
    create_session,
)
from db import UserRepository, LoginAttemptRepository, HACCPDatabase

logger = logging.getLogger(__name__)

# Cookie name constant
SESSION_COOKIE_NAME = "haccp_session"


def render_login_page(
    db: HACCPDatabase,
    cookie_manager=None,
    cookie_duration_days: int = 7,
):
    """
    Render the login page with authentication form.

    :param db: HACCPDatabase instance
    :param cookie_manager: Optional CookieManager instance for persistent login
    :param cookie_duration_days: Cookie duration in days (default 7)
    """
    st.title("Digitale HACCP-App")
    st.subheader("Anmeldung")

    # Initialize repositories
    user_repo = UserRepository(db)
    attempt_repo = LoginAttemptRepository(db)

    # Get client IP
    ip_address = get_client_ip()

    # Check if IP is locked out
    if attempt_repo.check_lockout(ip_address):
        st.error(
            "Zu viele fehlgeschlagene Anmeldeversuche. "
            "Bitte warten Sie 15 Minuten und versuchen Sie es erneut."
        )
        logger.warning(f"Login attempt from locked IP: {ip_address}")
        return

    # Login form
    with st.form("login_form"):
        username = st.text_input("Benutzername")
        password = st.text_input("Passwort", type="password")
        remember_me = st.checkbox("Angemeldet bleiben", value=False)

        submitted = st.form_submit_button("Anmelden", width="stretch")

        if submitted:
            if not username or not password:
                st.error("Bitte Benutzername und Passwort eingeben.")
                return

            # Look up user
            user = user_repo.get_by_username(username)

            if not user:
                # Record failed attempt (don't reveal if user exists)
                attempt_repo.record_attempt(ip_address, username, success=False)
                st.error("Ungültige Anmeldedaten.")
                logger.info(f"Failed login for unknown user: {username} from {ip_address}")
                return

            if not user.active:
                attempt_repo.record_attempt(ip_address, username, success=False)
                st.error("Dieses Konto ist deaktiviert.")
                logger.info(f"Login attempt for inactive user: {username}")
                return

            # Verify password
            if not verify_password(user.password_hash, user.password_salt, password):
                attempt_repo.record_attempt(ip_address, username, success=False)
                st.error("Ungültige Anmeldedaten.")
                logger.info(f"Failed login for user: {username} from {ip_address}")
                return

            # Successful login
            duration_hours = 168 if remember_me else 8  # 7 days or 8 hours

            session_key = create_session(user.id, db, duration_hours)
            if not session_key:
                st.error("Fehler beim Erstellen der Sitzung. Bitte erneut versuchen.")
                return

            # Record successful login
            attempt_repo.record_attempt(ip_address, username, success=True)

            # Store session in Streamlit session state
            st.session_state["session_key"] = session_key
            st.session_state["user_id"] = user.id
            st.session_state["user_role"] = user.role
            st.session_state["user_display_name"] = user.display_name or user.username

            # Store session key in cookie for persistent login
            if remember_me and cookie_manager is not None:
                expires = datetime.now() + timedelta(days=cookie_duration_days)
                cookie_manager.set(
                    SESSION_COOKIE_NAME,
                    session_key,
                    expires_at=expires,
                )
                logger.info(f"Session cookie set for user {username}")

            logger.info(f"User {username} logged in from {ip_address}")

            # Rerun to show main app
            st.rerun()

    # Footer info
    st.markdown("---")
    st.caption("HACCP Digitale Dokumentation")

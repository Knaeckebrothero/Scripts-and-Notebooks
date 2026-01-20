"""
Session management for HACCP authentication.
Handles session creation, validation, and logout.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

import streamlit as st

from .security import generate_session_key, get_client_ip
from .models import User, Session

logger = logging.getLogger(__name__)


def create_session(
    user_id: int,
    db,
    duration_hours: int = 8,
) -> Optional[str]:
    """
    Create a new session for a user.

    :param user_id: User ID to create session for
    :param db: HACCPDatabase instance
    :param duration_hours: Session duration in hours
    :return: Session key if successful, None otherwise
    """
    session_key = generate_session_key()
    expires_at = (datetime.now() + timedelta(hours=duration_hours)).isoformat()
    ip_address = get_client_ip()

    try:
        db.execute(
            """INSERT INTO sessions (user_id, session_key, expires_at, ip_address)
               VALUES (%s, %s, %s, %s)""",
            (user_id, session_key, expires_at, ip_address),
        )

        # Update user's last login
        db.execute(
            "UPDATE users SET last_login = %s WHERE id = %s",
            (datetime.now().isoformat(), user_id),
        )

        logger.info(f"Created session for user {user_id}")
        return session_key
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        return None


def validate_session(session_key: str, db) -> Optional[int]:
    """
    Validate a session key and return the user_id if valid.
    Deletes expired sessions.

    :param session_key: Session key to validate
    :param db: HACCPDatabase instance
    :return: User ID if valid, None otherwise
    """
    if not session_key:
        return None

    try:
        cursor = db.execute(
            "SELECT user_id, expires_at FROM sessions WHERE session_key = %s",
            (session_key,),
            commit=False,
        )
        row = cursor.fetchone()

        if not row:
            logger.debug(f"Session not found: {session_key[:8]}...")
            return None

        user_id, expires_at = row["user_id"], row["expires_at"]
        expires_dt = datetime.fromisoformat(expires_at)

        if expires_dt < datetime.now():
            logger.info(f"Session expired for user {user_id}")
            db.execute("DELETE FROM sessions WHERE session_key = %s", (session_key,))
            return None

        return user_id
    except Exception as e:
        logger.error(f"Error validating session: {e}")
        return None


def logout(session_key: str, db) -> bool:
    """
    Log out by removing the session from the database.

    :param session_key: Session key to invalidate
    :param db: HACCPDatabase instance
    :return: True if successful
    """
    try:
        db.execute("DELETE FROM sessions WHERE session_key = %s", (session_key,))
        logger.info(f"User logged out: {session_key[:8]}...")
        return True
    except Exception as e:
        logger.error(f"Error during logout: {e}")
        return False


def is_authenticated(db) -> bool:
    """
    Check if the current session is authenticated.

    :param db: HACCPDatabase instance
    :return: True if authenticated
    """
    session_key = st.session_state.get("session_key")
    if not session_key:
        return False

    user_id = validate_session(session_key, db)
    if not user_id:
        # Clear invalid session from state
        st.session_state.pop("session_key", None)
        st.session_state.pop("user_id", None)
        st.session_state.pop("user_role", None)
        return False

    return True


def get_current_user(db) -> Optional[User]:
    """
    Get the currently authenticated user.

    :param db: HACCPDatabase instance
    :return: User object if authenticated, None otherwise
    """
    session_key = st.session_state.get("session_key")
    if not session_key:
        return None

    user_id = validate_session(session_key, db)
    if not user_id:
        return None

    try:
        cursor = db.execute(
            "SELECT * FROM users WHERE id = %s",
            (user_id,),
            commit=False,
        )
        row = cursor.fetchone()
        if row:
            return User.from_row(dict(row))
        return None
    except Exception as e:
        logger.error(f"Error getting current user: {e}")
        return None

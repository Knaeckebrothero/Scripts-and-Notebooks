"""
HACCP Application - Main Entry Point
Digital HACCP compliance tracking for hotel/hospitality.
"""
import json
import logging
from pathlib import Path

import streamlit as st

from config import CLEANING_SCHEDULE
from db import HACCPDatabase, get_db
from ui.components import render_alerts_sidebar
from ui.pages import (
    render_kitchen_page,
    render_housekeeping_page,
    render_hotel_page,
    render_login_page,
    render_admin_page,
)
from auth import is_authenticated, logout, get_allowed_pages

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def init_session_state():
    """Initialize session state defaults."""
    defaults = {
        "last_saved": None,
        "show_admin": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_landing_page(db: HACCPDatabase, allowed: list, cleaning_schedule: dict):
    """Render the main landing page with expanders."""
    st.title("📋 Digitale HACCP-App")

    if "kitchen" in allowed:
        with st.expander("🍳 Küche – HACCP", expanded=False):
            render_kitchen_page(db, cleaning_schedule)

    if "housekeeping" in allowed:
        with st.expander("🧹 Housekeeping – HACCP", expanded=False):
            render_housekeeping_page(db)

    if "hotel" in allowed:
        with st.expander("🏨 Hotel – Allgemein", expanded=False):
            render_hotel_page(db)


def save_to_json(db: HACCPDatabase):
    """Export all database tables to JSON file."""
    tables = [
        "kitchen_temperature",
        "kitchen_goods_receipt",
        "kitchen_open_products",
        "kitchen_cleaning",
        "housekeeping",
        "housekeeping_basic_cleaning",
        "hotel_guests",
        "hotel_arrival_control",
    ]

    data = {}
    for tbl in tables:
        cursor = db.execute(f"SELECT * FROM {tbl}", commit=False)
        rows = [dict(row) for row in cursor.fetchall()]
        data[tbl] = rows

    export_path = Path(__file__).parent / "haccp_export.json"
    export_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    st.session_state.last_saved = str(export_path)
    st.success(f"Daten wurden nach **{export_path}** exportiert.")


def handle_logout(db: HACCPDatabase):
    """Handle user logout."""
    session_key = st.session_state.get("session_key")
    if session_key:
        logout(session_key, db)

    # Clear session state
    for key in ["session_key", "user_id", "user_role", "user_display_name"]:
        st.session_state.pop(key, None)

    st.rerun()


def main():
    """Main application entry point."""
    # Page configuration
    st.set_page_config(
        page_title="Digitale HACCP-App",
        page_icon="📋",
        layout="wide",
    )

    # Initialize
    init_session_state()
    db = get_db()

    # Check authentication
    if not is_authenticated(db):
        render_login_page(db)
        return

    # Sidebar navigation
    st.sidebar.title("📋 HACCP-Navigation")

    # User info and logout
    display_name = st.session_state.get("user_display_name", "Benutzer")
    st.sidebar.markdown(f"Angemeldet als: **{display_name}**")

    if st.sidebar.button("🚪 Abmelden"):
        handle_logout(db)

    st.sidebar.markdown("---")

    # Build navigation options based on role
    user_role = st.session_state.get("user_role", "staff")
    allowed = get_allowed_pages(user_role)

    # Show message if no pages accessible
    has_content = any(p in allowed for p in ["kitchen", "housekeeping", "hotel", "admin"])
    if not has_content:
        st.sidebar.warning("Keine Bereiche verfügbar für Ihre Rolle.")
        st.warning(
            "Sie haben keine Berechtigung für spezifische Bereiche. "
            "Bitte wenden Sie sich an einen Administrator."
        )
        return

    # Admin access via sidebar button
    if "admin" in allowed:
        if st.session_state.get("show_admin"):
            if st.sidebar.button("← Zurück zur Übersicht"):
                st.session_state.show_admin = False
                st.rerun()
        else:
            if st.sidebar.button("👤 Benutzerverwaltung"):
                st.session_state.show_admin = True
                st.rerun()

    # Render alerts in sidebar
    render_alerts_sidebar(db, CLEANING_SCHEDULE)

    # Export button
    st.sidebar.markdown("---")
    if st.sidebar.button("💾 Daten als JSON exportieren"):
        save_to_json(db)

    if st.session_state.get("last_saved"):
        st.sidebar.info(f"Zuletzt gespeichert: **{st.session_state.last_saved}**")

    # Render appropriate page
    if st.session_state.get("show_admin") and "admin" in allowed:
        render_admin_page(db)
    else:
        render_landing_page(db, allowed, CLEANING_SCHEDULE)


if __name__ == "__main__":
    main()

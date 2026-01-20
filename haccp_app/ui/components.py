"""
Reusable Streamlit components for HACCP application.
"""
from datetime import date, timedelta
from typing import Tuple

import streamlit as st

from db import HACCPDatabase
from services import AlertService, AlertPriority


def render_alerts_sidebar(db: HACCPDatabase, cleaning_schedule: dict):
    """
    Display alerts in sidebar with color coding.
    Shows expiring products and overdue cleaning tasks.
    """
    alert_service = AlertService(db)
    alerts = alert_service.get_all_alerts(cleaning_schedule)

    st.sidebar.markdown("---")

    if alerts:
        st.sidebar.markdown("### Warnungen")

        critical_count = sum(1 for a in alerts if a.priority == AlertPriority.CRITICAL)
        high_count = sum(1 for a in alerts if a.priority == AlertPriority.HIGH)

        if critical_count > 0:
            st.sidebar.error(f"{critical_count} kritische Warnung(en)")
        if high_count > 0:
            st.sidebar.warning(f"{high_count} wichtige Warnung(en)")

        with st.sidebar.expander(f"Alle Warnungen ({len(alerts)})", expanded=critical_count > 0):
            for alert in alerts:
                if alert.priority == AlertPriority.CRITICAL:
                    st.error(alert.message)
                elif alert.priority == AlertPriority.HIGH:
                    st.warning(alert.message)
                else:
                    st.info(alert.message)
    else:
        st.sidebar.success("Keine Warnungen")


def render_date_range_picker(
    key_prefix: str = "report",
    default_days: int = 30,
) -> Tuple[date, date]:
    """
    Render date range picker with start and end date.

    Returns:
        Tuple of (start_date, end_date)
    """
    col1, col2 = st.columns(2)

    with col1:
        start_date = st.date_input(
            "Von",
            value=date.today() - timedelta(days=default_days),
            key=f"{key_prefix}_start",
        )

    with col2:
        end_date = st.date_input(
            "Bis",
            value=date.today(),
            key=f"{key_prefix}_end",
        )

    return start_date, end_date


def render_success_message(message: str):
    """Show a success message that auto-clears."""
    st.success(message)


def render_data_table(df, title: str = None):
    """Render a dataframe with optional title."""
    if title:
        st.subheader(title)

    if df is not None and not df.empty:
        st.dataframe(df, use_container_width=True)
    else:
        st.info("Keine Einträge vorhanden.")

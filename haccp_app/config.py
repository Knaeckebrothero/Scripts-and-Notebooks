"""
Configuration for HACCP application.
Contains schedules, thresholds, and settings.
"""
from dataclasses import dataclass
from typing import Optional

# Cleaning schedule with frequencies
# frequency_days: how often the station should be cleaned
# label: display name in German
# tasks: available cleaning task options
CLEANING_SCHEDULE = {
    "kaffeemaschine": {
        "label": "Kaffeemaschine",
        "frequency_days": 1,
        "tasks": [
            "Oberfläche abwischen",
            "Entkalken",
            "Filter prüfen/wechseln",
            "Tägliche Reinigung",
        ],
    },
    "teestation": {
        "label": "Teestation",
        "frequency_days": 1,
        "tasks": [
            "Oberfläche abwischen",
            "Kannen reinigen",
            "Tägliche Reinigung",
        ],
    },
    "buffet": {
        "label": "Buffet",
        "frequency_days": 1,
        "tasks": [
            "Oberfläche abwischen",
            "Tägliche Reinigung",
            "Wöchentliche Tiefenreinigung",
        ],
    },
    "eierstation": {
        "label": "Eierstation",
        "frequency_days": 1,
        "tasks": [
            "Oberfläche abwischen",
            "Tägliche Reinigung",
            "Geräte reinigen",
        ],
    },
}

# Alert thresholds
ALERT_EXPIRY_WARN_DAYS = 3  # Warn about products expiring within N days

# Temperature thresholds (for future validation)
FRIDGE_TEMP_MIN = 0.0
FRIDGE_TEMP_MAX = 7.0
FREEZER_TEMP_MIN = -25.0
FREEZER_TEMP_MAX = -18.0


@dataclass
class UserContext:
    """
    User context for multi-user authentication.
    Integrates with session state from auth module.
    """

    user_id: Optional[int] = None
    username: str = "default"
    role: str = "staff"  # "admin", "kitchen_staff", "housekeeping", "manager", "staff"

    @classmethod
    def get_current(cls) -> "UserContext":
        """Get current user from session state."""
        import streamlit as st

        return cls(
            user_id=st.session_state.get("user_id"),
            username=st.session_state.get("user_display_name", "default"),
            role=st.session_state.get("user_role", "staff"),
        )

    def is_admin(self) -> bool:
        return self.role == "admin"

    def can_access_kitchen(self) -> bool:
        return self.role in ("admin", "kitchen_staff", "manager")

    def can_access_housekeeping(self) -> bool:
        return self.role in ("admin", "housekeeping", "manager")

    def can_access_hotel(self) -> bool:
        return self.role in ("admin", "manager")

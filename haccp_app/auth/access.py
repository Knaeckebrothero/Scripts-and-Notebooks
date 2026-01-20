"""
Role-based access control for HACCP application.
Defines roles, permissions, and access control utilities.
"""
from functools import wraps
from typing import Callable

import streamlit as st


# Role definitions with feature access
# "all" grants access to all features
ROLES = {
    "admin": {"features": {"all"}},
    "manager": {"features": {"kitchen", "housekeeping", "hotel", "reports"}},
    "kitchen_staff": {"features": {"kitchen"}},
    "housekeeping": {"features": {"housekeeping"}},
    "staff": {"features": set()},  # View only - no feature access
}

# Page name to feature mapping
PAGE_FEATURES = {
    "Küche – HACCP": "kitchen",
    "Housekeeping – HACCP": "housekeeping",
    "Hotel – Allgemein": "hotel",
    "Admin – Benutzerverwaltung": "admin",
}

# All available features
ALL_FEATURES = {"kitchen", "housekeeping", "hotel", "admin", "reports"}


def has_feature(role: str, feature: str) -> bool:
    """
    Check if a role has access to a specific feature.

    :param role: User role name
    :param feature: Feature name to check
    :return: True if role has access to feature
    """
    if role not in ROLES:
        return False

    role_features = ROLES[role]["features"]

    # "all" grants access to everything
    if "all" in role_features:
        return True

    return feature in role_features


def get_allowed_pages(role: str) -> list[str]:
    """
    Get list of pages a role can access.

    :param role: User role name
    :return: List of allowed feature names
    """
    if role not in ROLES:
        return []

    role_features = ROLES[role]["features"]

    # "all" grants access to all features
    if "all" in role_features:
        return list(ALL_FEATURES)

    return list(role_features)


def get_available_roles() -> list[str]:
    """
    Get list of all available role names.

    :return: List of role names
    """
    return list(ROLES.keys())


def require_role(allowed_roles: list[str]) -> Callable:
    """
    Decorator for page functions that require specific roles.
    Shows error message if user doesn't have required role.

    :param allowed_roles: List of roles allowed to access the page
    :return: Decorator function
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            user_role = st.session_state.get("user_role", "staff")

            # Check if user's role is in allowed roles
            if user_role not in allowed_roles:
                st.error("⛔ Zugriff verweigert")
                st.warning(
                    "Sie haben keine Berechtigung für diesen Bereich. "
                    "Bitte wenden Sie sich an einen Administrator."
                )
                return

            return func(*args, **kwargs)

        return wrapper

    return decorator


def can_access_page(role: str, page_name: str) -> bool:
    """
    Check if a role can access a specific page.

    :param role: User role name
    :param page_name: Page display name
    :return: True if role can access the page
    """
    feature = PAGE_FEATURES.get(page_name)
    if not feature:
        return False

    return has_feature(role, feature)

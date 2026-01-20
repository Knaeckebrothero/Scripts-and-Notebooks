"""Authentication module for HACCP application."""
from .models import User, Session, LoginAttempt
from .security import (
    hash_password,
    verify_password,
    generate_session_key,
    generate_secure_password,
    get_client_ip,
)
from .session import (
    create_session,
    validate_session,
    logout,
    is_authenticated,
    get_current_user,
)
from .access import (
    ROLES,
    has_feature,
    get_allowed_pages,
    get_available_roles,
    require_role,
    can_access_page,
)

__all__ = [
    # Models
    "User",
    "Session",
    "LoginAttempt",
    # Security functions
    "hash_password",
    "verify_password",
    "generate_session_key",
    "generate_secure_password",
    "get_client_ip",
    # Session management
    "create_session",
    "validate_session",
    "logout",
    "is_authenticated",
    "get_current_user",
    # Access control
    "ROLES",
    "has_feature",
    "get_allowed_pages",
    "get_available_roles",
    "require_role",
    "can_access_page",
]

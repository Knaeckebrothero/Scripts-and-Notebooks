"""
Data models for authentication entities.
Using dataclasses for type safety and easy dict conversion.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


def _now_iso() -> str:
    return datetime.now().isoformat()


@dataclass
class User:
    """User account for authentication."""

    username: str
    password_hash: str
    password_salt: str
    role: str = "staff"
    display_name: Optional[str] = None
    active: bool = True
    created_at: Optional[str] = None
    last_login: Optional[str] = None
    id: Optional[int] = None

    def to_dict(self) -> dict:
        """Convert to dict for database insertion."""
        return {
            "username": self.username,
            "password_hash": self.password_hash,
            "password_salt": self.password_salt,
            "role": self.role,
            "display_name": self.display_name,
            "active": 1 if self.active else 0,
        }

    @classmethod
    def from_row(cls, row: dict) -> "User":
        """Create User from database row."""
        return cls(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            password_salt=row["password_salt"],
            role=row["role"],
            display_name=row.get("display_name"),
            active=bool(row.get("active", 1)),
            created_at=row.get("created_at"),
            last_login=row.get("last_login"),
        )


@dataclass
class Session:
    """Active user session."""

    user_id: int
    session_key: str
    expires_at: str
    ip_address: Optional[str] = None
    created_at: Optional[str] = None
    id: Optional[int] = None

    def to_dict(self) -> dict:
        """Convert to dict for database insertion."""
        return {
            "user_id": self.user_id,
            "session_key": self.session_key,
            "expires_at": self.expires_at,
            "ip_address": self.ip_address,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Session":
        """Create Session from database row."""
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            session_key=row["session_key"],
            expires_at=row["expires_at"],
            ip_address=row.get("ip_address"),
            created_at=row.get("created_at"),
        )


@dataclass
class LoginAttempt:
    """Login attempt record for rate limiting."""

    ip_address: str
    username: Optional[str] = None
    success: bool = False
    attempted_at: Optional[str] = None
    id: Optional[int] = None

    def to_dict(self) -> dict:
        """Convert to dict for database insertion."""
        return {
            "ip_address": self.ip_address,
            "username": self.username,
            "success": 1 if self.success else 0,
        }

    @classmethod
    def from_row(cls, row: dict) -> "LoginAttempt":
        """Create LoginAttempt from database row."""
        return cls(
            id=row["id"],
            ip_address=row["ip_address"],
            username=row.get("username"),
            success=bool(row.get("success", 0)),
            attempted_at=row.get("attempted_at"),
        )

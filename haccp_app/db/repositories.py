"""
Repository classes for HACCP database operations.
Eliminates repetitive insert/select code with generic base + domain-specific queries.
"""
import logging
from datetime import date, datetime, timedelta
from typing import TypeVar, Generic, Type, List, Optional, Callable
import pandas as pd

from .database import HACCPDatabase

logger = logging.getLogger(__name__)
from .models import (
    KitchenTemperature,
    GoodsReceipt,
    OpenProduct,
    KitchenCleaning,
    Housekeeping,
    BasicCleaning,
    HotelGuest,
    ArrivalControl,
)
from auth.models import User, Session, LoginAttempt

T = TypeVar("T")


class BaseRepository(Generic[T]):
    """Generic repository with common CRUD operations."""

    def __init__(
        self,
        db: HACCPDatabase,
        table: str,
        model_class: Type[T],
        from_row: Callable[[dict], T] = None,
    ):
        self.db = db
        self.table = table
        self.model_class = model_class
        self._from_row = from_row

    def insert(self, entity: T) -> int:
        """Insert entity and return its ID."""
        data = entity.to_dict()
        columns = ", ".join(data.keys())
        placeholders = ", ".join(["%s"] * len(data))

        cursor = self.db.execute(
            f"INSERT INTO {self.table} ({columns}) VALUES ({placeholders}) RETURNING id",
            tuple(data.values()),
        )
        row = cursor.fetchone()
        inserted_id = row["id"] if row else None
        logger.debug(f"Inserted into {self.table}, id={inserted_id}")
        return inserted_id

    def get_all(self, order_by: str = "id DESC", limit: int = None) -> List[T]:
        """Get all records."""
        query = f"SELECT * FROM {self.table} ORDER BY {order_by}"
        if limit:
            query += f" LIMIT {limit}"

        cursor = self.db.execute(query, commit=False)
        rows = cursor.fetchall()
        logger.debug(f"Retrieved {len(rows)} records from {self.table}")

        if self._from_row:
            return [self._from_row(dict(row)) for row in rows]
        return [self.model_class(**dict(row)) for row in rows]

    def get_by_id(self, id: int) -> Optional[T]:
        """Get single record by ID."""
        cursor = self.db.execute(
            f"SELECT * FROM {self.table} WHERE id = %s", (id,), commit=False
        )
        row = cursor.fetchone()
        if not row:
            return None

        if self._from_row:
            return self._from_row(dict(row))
        return self.model_class(**dict(row))

    def delete(self, id: int) -> bool:
        """Delete record by ID."""
        cursor = self.db.execute(f"DELETE FROM {self.table} WHERE id = %s", (id,))
        deleted = cursor.rowcount > 0
        if deleted:
            logger.debug(f"Deleted record id={id} from {self.table}")
        else:
            logger.warning(f"No record with id={id} found in {self.table}")
        return deleted

    def get_dataframe(self, query: str = None, params: tuple = None) -> pd.DataFrame:
        """Get results as pandas DataFrame."""
        if query is None:
            query = f"SELECT * FROM {self.table}"
        return pd.read_sql_query(query, self.db.connection, params=params)


class KitchenTemperatureRepo(BaseRepository[KitchenTemperature]):
    """Repository for kitchen temperature logs."""

    def __init__(self, db: HACCPDatabase):
        super().__init__(db, "kitchen_temperature", KitchenTemperature)

    def get_by_location(
        self, location: str, days: int = 7
    ) -> List[KitchenTemperature]:
        """Get temperature logs for a specific location within N days."""
        since = (datetime.now() - timedelta(days=days)).isoformat()
        cursor = self.db.execute(
            """SELECT * FROM kitchen_temperature
               WHERE location = %s AND timestamp >= %s
               ORDER BY timestamp DESC""",
            (location, since),
            commit=False,
        )
        return [KitchenTemperature(**dict(row)) for row in cursor.fetchall()]

    def get_by_date_range(
        self, start: date, end: date, location: str = None
    ) -> List[KitchenTemperature]:
        """Get temperatures within date range, optionally filtered by location."""
        start_str = start.isoformat()
        end_str = (end + timedelta(days=1)).isoformat()

        if location:
            cursor = self.db.execute(
                """SELECT * FROM kitchen_temperature
                   WHERE timestamp >= %s AND timestamp < %s AND location = %s
                   ORDER BY timestamp ASC""",
                (start_str, end_str, location),
                commit=False,
            )
        else:
            cursor = self.db.execute(
                """SELECT * FROM kitchen_temperature
                   WHERE timestamp >= %s AND timestamp < %s
                   ORDER BY timestamp ASC""",
                (start_str, end_str),
                commit=False,
            )
        return [KitchenTemperature(**dict(row)) for row in cursor.fetchall()]


class GoodsReceiptRepo(BaseRepository[GoodsReceipt]):
    """Repository for goods receipt records."""

    def __init__(self, db: HACCPDatabase):
        super().__init__(db, "kitchen_goods_receipt", GoodsReceipt)

    def get_by_date_range(self, start: date, end: date) -> List[GoodsReceipt]:
        """Get receipts within date range."""
        cursor = self.db.execute(
            """SELECT * FROM kitchen_goods_receipt
               WHERE receipt_date >= %s AND receipt_date <= %s
               ORDER BY receipt_date DESC""",
            (start.isoformat(), end.isoformat()),
            commit=False,
        )
        return [GoodsReceipt(**dict(row)) for row in cursor.fetchall()]


class OpenProductRepo(BaseRepository[OpenProduct]):
    """Repository for open food products."""

    def __init__(self, db: HACCPDatabase):
        super().__init__(db, "kitchen_open_products", OpenProduct)

    def get_expiring_soon(self, days: int = 3) -> List[OpenProduct]:
        """Get products expiring within N days (including already expired)."""
        threshold = (date.today() + timedelta(days=days)).isoformat()
        cursor = self.db.execute(
            """SELECT * FROM kitchen_open_products
               WHERE expiry_date <= %s
               ORDER BY expiry_date ASC""",
            (threshold,),
            commit=False,
        )
        return [OpenProduct(**dict(row)) for row in cursor.fetchall()]

    def get_expired(self) -> List[OpenProduct]:
        """Get already expired products."""
        today = date.today().isoformat()
        cursor = self.db.execute(
            """SELECT * FROM kitchen_open_products
               WHERE expiry_date < %s
               ORDER BY expiry_date ASC""",
            (today,),
            commit=False,
        )
        return [OpenProduct(**dict(row)) for row in cursor.fetchall()]


class KitchenCleaningRepo(BaseRepository[KitchenCleaning]):
    """Repository for kitchen cleaning records."""

    def __init__(self, db: HACCPDatabase):
        super().__init__(
            db, "kitchen_cleaning", KitchenCleaning, KitchenCleaning.from_row
        )

    def get_last_for_station(self, station: str) -> Optional[KitchenCleaning]:
        """Get the most recent cleaning record for a station."""
        cursor = self.db.execute(
            """SELECT * FROM kitchen_cleaning
               WHERE station = %s
               ORDER BY completed_at DESC LIMIT 1""",
            (station,),
            commit=False,
        )
        row = cursor.fetchone()
        return KitchenCleaning.from_row(dict(row)) if row else None

    def get_by_station(self, station: str, limit: int = 10) -> List[KitchenCleaning]:
        """Get cleaning records for a specific station."""
        cursor = self.db.execute(
            """SELECT * FROM kitchen_cleaning
               WHERE station = %s
               ORDER BY completed_at DESC LIMIT %s""",
            (station, limit),
            commit=False,
        )
        return [KitchenCleaning.from_row(dict(row)) for row in cursor.fetchall()]


class HousekeepingRepo(BaseRepository[Housekeeping]):
    """Repository for housekeeping records."""

    def __init__(self, db: HACCPDatabase):
        super().__init__(db, "housekeeping", Housekeeping, Housekeeping.from_row)

    def get_by_room(self, raum: str, limit: int = 10) -> List[Housekeeping]:
        """Get housekeeping records for a specific room."""
        cursor = self.db.execute(
            """SELECT * FROM housekeeping
               WHERE raum = %s
               ORDER BY datum DESC LIMIT %s""",
            (raum, limit),
            commit=False,
        )
        return [Housekeeping.from_row(dict(row)) for row in cursor.fetchall()]


class BasicCleaningRepo(BaseRepository[BasicCleaning]):
    """Repository for deep/basic cleaning records."""

    def __init__(self, db: HACCPDatabase):
        super().__init__(db, "housekeeping_basic_cleaning", BasicCleaning)


class HotelGuestRepo(BaseRepository[HotelGuest]):
    """Repository for hotel guest records."""

    def __init__(self, db: HACCPDatabase):
        super().__init__(db, "hotel_guests", HotelGuest, HotelGuest.from_row)

    def get_current_guests(self) -> List[HotelGuest]:
        """Get guests currently staying (arrived and not yet departed)."""
        today = date.today().isoformat()
        cursor = self.db.execute(
            """SELECT * FROM hotel_guests
               WHERE anreise <= %s AND abreise >= %s
               ORDER BY abreise ASC""",
            (today, today),
            commit=False,
        )
        return [HotelGuest.from_row(dict(row)) for row in cursor.fetchall()]

    def get_arriving_today(self) -> List[HotelGuest]:
        """Get guests arriving today."""
        today = date.today().isoformat()
        cursor = self.db.execute(
            """SELECT * FROM hotel_guests
               WHERE anreise = %s
               ORDER BY name ASC""",
            (today,),
            commit=False,
        )
        return [HotelGuest.from_row(dict(row)) for row in cursor.fetchall()]


class ArrivalControlRepo(BaseRepository[ArrivalControl]):
    """Repository for arrival control records."""

    def __init__(self, db: HACCPDatabase):
        super().__init__(db, "hotel_arrival_control", ArrivalControl)

    def get_by_date(self, datum: date) -> List[ArrivalControl]:
        """Get arrival controls for a specific date."""
        cursor = self.db.execute(
            """SELECT * FROM hotel_arrival_control
               WHERE datum = %s
               ORDER BY zimmer ASC""",
            (datum.isoformat(),),
            commit=False,
        )
        return [ArrivalControl(**dict(row)) for row in cursor.fetchall()]


# ============================================
# AUTH REPOSITORIES
# ============================================


class UserRepository(BaseRepository[User]):
    """Repository for user accounts."""

    def __init__(self, db: HACCPDatabase):
        super().__init__(db, "users", User, User.from_row)

    def get_by_username(self, username: str) -> Optional[User]:
        """Get user by username."""
        cursor = self.db.execute(
            "SELECT * FROM users WHERE username = %s",
            (username,),
            commit=False,
        )
        row = cursor.fetchone()
        return User.from_row(dict(row)) if row else None

    def update_last_login(self, user_id: int) -> bool:
        """Update user's last login timestamp."""
        try:
            self.db.execute(
                "UPDATE users SET last_login = %s WHERE id = %s",
                (datetime.now().isoformat(), user_id),
            )
            return True
        except Exception as e:
            logger.error(f"Error updating last login: {e}")
            return False

    def get_active_users(self) -> List[User]:
        """Get all active users."""
        cursor = self.db.execute(
            "SELECT * FROM users WHERE active = TRUE ORDER BY username ASC",
            commit=False,
        )
        return [User.from_row(dict(row)) for row in cursor.fetchall()]


class SessionRepository(BaseRepository[Session]):
    """Repository for user sessions."""

    def __init__(self, db: HACCPDatabase):
        super().__init__(db, "sessions", Session, Session.from_row)

    def get_by_session_key(self, session_key: str) -> Optional[Session]:
        """Get session by session key."""
        cursor = self.db.execute(
            "SELECT * FROM sessions WHERE session_key = %s",
            (session_key,),
            commit=False,
        )
        row = cursor.fetchone()
        return Session.from_row(dict(row)) if row else None

    def delete_expired(self) -> int:
        """Delete all expired sessions and return count."""
        now = datetime.now().isoformat()
        cursor = self.db.execute(
            "DELETE FROM sessions WHERE expires_at < %s",
            (now,),
        )
        count = cursor.rowcount
        if count > 0:
            logger.info(f"Deleted {count} expired sessions")
        return count

    def delete_for_user(self, user_id: int) -> int:
        """Delete all sessions for a user (force logout)."""
        cursor = self.db.execute(
            "DELETE FROM sessions WHERE user_id = %s",
            (user_id,),
        )
        return cursor.rowcount


class LoginAttemptRepository(BaseRepository[LoginAttempt]):
    """Repository for login attempt tracking."""

    def __init__(self, db: HACCPDatabase):
        super().__init__(db, "login_attempts", LoginAttempt, LoginAttempt.from_row)

    def record_attempt(
        self, ip_address: str, username: str = None, success: bool = False
    ) -> int:
        """Record a login attempt."""
        attempt = LoginAttempt(
            ip_address=ip_address,
            username=username,
            success=success,
        )
        return self.insert(attempt)

    def check_lockout(
        self, ip_address: str, max_attempts: int = 5, window_minutes: int = 15
    ) -> bool:
        """
        Check if an IP address is locked out due to too many failed attempts.

        :param ip_address: IP address to check
        :param max_attempts: Maximum allowed failed attempts
        :param window_minutes: Time window in minutes
        :return: True if locked out, False otherwise
        """
        if ip_address == "unknown":
            return False

        window_start = (datetime.now() - timedelta(minutes=window_minutes)).isoformat()

        cursor = self.db.execute(
            """SELECT COUNT(*) as count FROM login_attempts
               WHERE ip_address = %s
               AND attempted_at > %s
               AND success = FALSE""",
            (ip_address, window_start),
            commit=False,
        )
        row = cursor.fetchone()
        failed_count = row["count"] if row else 0

        if failed_count >= max_attempts - 1:
            logger.warning(
                f"IP {ip_address} has {failed_count} failed login attempts"
            )

        return failed_count >= max_attempts

    def cleanup_old(self, days: int = 30) -> int:
        """Delete login attempts older than N days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cursor = self.db.execute(
            "DELETE FROM login_attempts WHERE attempted_at < %s",
            (cutoff,),
        )
        count = cursor.rowcount
        if count > 0:
            logger.info(f"Cleaned up {count} old login attempts")
        return count

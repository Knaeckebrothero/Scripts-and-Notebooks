"""
HACCP Database Manager
Singleton PostgreSQL database with schema initialization from file.
"""
import logging
import os
from pathlib import Path
from typing import Optional, Tuple, Any, List

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# Required tables for the HACCP application
REQUIRED_TABLES = [
    "users",
    "sessions",
    "login_attempts",
    "kitchen_temperature",
    "kitchen_goods_receipt",
    "kitchen_open_products",
    "kitchen_cleaning",
    "housekeeping",
    "housekeeping_basic_cleaning",
    "hotel_guests",
    "hotel_arrival_control",
]


class HACCPDatabase:
    """Singleton database manager for HACCP application."""

    _instance: Optional["HACCPDatabase"] = None

    @classmethod
    def get_instance(
        cls,
        host: str = "localhost",
        port: int = 5432,
        database: str = "haccp",
        user: str = "postgres",
        password: str = "postgres",
    ) -> "HACCPDatabase":
        """Get or create the singleton database instance."""
        if cls._instance is None:
            cls._instance = cls(host, port, database, user, password)
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        if cls._instance:
            cls._instance.close()
            cls._instance = None

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
    ):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self._conn: Optional[psycopg2.extensions.connection] = None
        self._connect()
        self._init_schema()
        self._verify_tables()

    def _connect(self):
        """Establish database connection."""
        try:
            self._conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
            )
            self._conn.autocommit = False
            logger.info(
                f"Connected to PostgreSQL database: {self.database}@{self.host}:{self.port}"
            )
        except psycopg2.Error as e:
            logger.error(f"Failed to connect to database: {e}")
            raise

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.debug("Database connection closed")

    @property
    def connection(self) -> psycopg2.extensions.connection:
        """Get the raw connection for pandas operations."""
        if not self._conn or self._conn.closed:
            self._connect()
        return self._conn

    def execute(
        self, query: str, params: Tuple[Any, ...] = None, commit: bool = True
    ):
        """Execute a query with error handling."""
        if not self._conn or self._conn.closed:
            self._connect()

        try:
            cursor = self._conn.cursor(cursor_factory=RealDictCursor)
            if params:
                logger.debug(f"Executing query with params: {query[:100]}...")
                cursor.execute(query, params)
            else:
                logger.debug(f"Executing query: {query[:100]}...")
                cursor.execute(query)

            if commit:
                self._conn.commit()

            return cursor

        except psycopg2.Error as e:
            logger.error(f"Database error: {e}")
            logger.debug(f"Failed query: {query}")
            if params:
                logger.debug(f"Query params: {params}")
            self._conn.rollback()
            raise

    def commit(self):
        """Commit current transaction."""
        if self._conn:
            self._conn.commit()
            logger.debug("Transaction committed")

    def rollback(self):
        """Rollback current transaction."""
        if self._conn:
            self._conn.rollback()
            logger.debug("Transaction rolled back")

    def _init_schema(self):
        """Load and execute schema from schema.sql file."""
        schema_path = Path(__file__).parent / "schema.sql"

        if not schema_path.exists():
            logger.error(f"Schema file not found: {schema_path}")
            raise FileNotFoundError(f"Schema file not found: {schema_path}")

        try:
            schema_sql = schema_path.read_text()
            logger.debug(f"Loading schema from {schema_path}")

            # Execute the entire schema as one transaction
            cursor = self._conn.cursor()
            cursor.execute(schema_sql)
            self._conn.commit()
            cursor.close()

            logger.info("Database schema initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize schema: {e}")
            self._conn.rollback()
            raise

    def _verify_tables(self, required_tables: List[str] = None):
        """
        Verify that all required tables exist in the database.

        :param required_tables: List of table names to verify
        :raises RuntimeError: If required tables are missing
        """
        if required_tables is None:
            required_tables = REQUIRED_TABLES

        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            )
            existing_tables = [row[0] for row in cursor.fetchall()]
            cursor.close()

            missing_tables = [t for t in required_tables if t not in existing_tables]

            if missing_tables:
                logger.error(f"Missing required tables: {missing_tables}")
                raise RuntimeError(f"Missing required tables: {missing_tables}")

            logger.info(f"Verified {len(required_tables)} tables exist")

        except psycopg2.Error as e:
            logger.error(f"Error verifying tables: {e}")
            raise

    def get_table_info(self, table_name: str) -> List[dict]:
        """Get column information for a table."""
        cursor = self._conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            SELECT
                ordinal_position as cid,
                column_name as name,
                data_type as type,
                CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END as notnull,
                column_default as default,
                CASE WHEN column_name IN (
                    SELECT column_name
                    FROM information_schema.key_column_usage
                    WHERE table_name = %s AND constraint_name LIKE '%%_pkey'
                ) THEN 1 ELSE 0 END as pk
            FROM information_schema.columns
            WHERE table_name = %s AND table_schema = 'public'
            ORDER BY ordinal_position
            """,
            (table_name, table_name),
        )
        columns = cursor.fetchall()
        cursor.close()
        return [dict(col) for col in columns]


def get_db() -> HACCPDatabase:
    """Convenience function to get database instance.

    Reads connection parameters from environment variables with fallback to defaults:
    - DATABASE_HOST (default: localhost)
    - DATABASE_PORT (default: 5432)
    - DATABASE_NAME (default: haccp)
    - DATABASE_USER (default: postgres)
    - DATABASE_PASSWORD (default: postgres)
    """
    return HACCPDatabase.get_instance(
        host=os.environ.get("DATABASE_HOST", "localhost"),
        port=int(os.environ.get("DATABASE_PORT", "5432")),
        database=os.environ.get("DATABASE_NAME", "haccp"),
        user=os.environ.get("DATABASE_USER", "postgres"),
        password=os.environ.get("DATABASE_PASSWORD", "postgres"),
    )

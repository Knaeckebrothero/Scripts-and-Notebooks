"""
Minimal SQLite Database Template
=================================
A simple, reusable database class for SQLite operations with singleton support.

Author: Assistant
Date: 2025-08-16
"""

import os
import sqlite3
import logging
import pandas as pd
from typing import Optional, List, Dict, Any, Tuple

# Set up logging
logger = logging.getLogger(__name__)

# TODO: How does the commit/rollback strategy work?
class DatabaseManager:
    """
    Simple SQLite database manager with basic CRUD operations.

    Commit Strategy:
        Each write operation (insert, update, delete) has an optional
        'commit' parameter. This gives you full control:

        - Single operations: db.insert(data, commit=True)
        - Batch operations: Multiple operations then db.commit()
        - Transactions: Multiple operations then db.commit() or db.rollback()

    Usage:
        db = DatabaseManager()

        # Single operation with immediate commit
        db.insert(name="John", email="john@example.com", commit=True)

        # Batch operations
        for record in records:
            db.insert(**record)  # No commit
        db.commit()  # Commit all at once

        # Transaction pattern
        try:
            db.insert(name="User1")
            db.update(1, status="active")
            db.commit()
        except Exception:
            db.rollback()
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize database connection using environment variables or defaults.

        Environment variables:
            DB_PATH: Path to SQLite database file
            DB_TIMEOUT: Connection timeout in seconds
        """
        # Get configuration from environment or use defaults
        self.db_path = db_path or os.getenv('DB_PATH', './database.db')
        self.timeout = float(os.getenv('DB_TIMEOUT', '5.0'))

        self._conn: Optional[sqlite3.Connection] = None

        # Ensure directory exists
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
            logger.debug(f"Created directory: {db_dir}")

        # Initialize connection
        self._connect()
        logger.info(f"Database initialized: {self.db_path}")

    # TODO: Check what this one is for
    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def __del__(self):
        """Cleanup on deletion."""
        self.close()

    def _connect(self):
        """
        Establish database connection with error handling.
        """
        try:
            self._conn = sqlite3.connect(
                self.db_path,
                timeout=self.timeout,
                check_same_thread=False
            )
            # Enable row factory for dictionary-like access
            self._conn.row_factory = sqlite3.Row
            logger.debug(f"Connected to database: {self.db_path}")

        except sqlite3.Error as e:
            logger.error(f"Failed to connect to database: {e}")
            raise

    def close(self):
        """Close database connection."""
        if self._conn:
            try:
                self._conn.close()
                self._conn = None
                logger.debug("Database connection closed")
            except sqlite3.Error as e:
                logger.error(f"Error closing connection: {e}")

    def _execute(self, query: str, params: Optional[Tuple] = None) -> sqlite3.Cursor:
        """
        Execute a query with error handling and logging.

        Args:
            query: SQL query string
            params: Query parameters for safe parameterization

        Returns:
            Cursor object with results
        """
        if not self._conn:
            self._connect()

        try:
            cursor = self._conn.cursor()

            if params:
                logger.debug(f"Executing query with params: {params}")
                cursor.execute(query, params)
            else:
                logger.debug("Executing query")
                cursor.execute(query)

            return cursor

        except sqlite3.Error as e:
            logger.error(f"Query execution failed: {e}")
            logger.debug(f"Query: {query}")
            if params:
                logger.debug(f"Params: {params}")
            raise

    def get_data(self, **kwargs) -> List[Dict[str, Any]]:
        """
        Fetch data from the database.

        Example:
            data = db.get_data(user_id=1, status='active')

        Args:
            **kwargs: Parameters for the WHERE clause

        Returns:
            List of dictionaries containing the results
        """
        # TODO: Replace with an actual hardcoded statement
        # Build WHERE clause from kwargs
        if kwargs:
            conditions = " AND ".join([f"{k} = ?" for k in kwargs.keys()])
            query = f"""
                SELECT * FROM your_table 
                WHERE {conditions}
            """
            params = tuple(kwargs.values())
        else:
            query = "SELECT * FROM your_table"
            params = None

        try:
            cursor = self._execute(query, params)
            results = cursor.fetchall()

            # Convert Row objects to dictionaries
            data = [dict(row) for row in results]
            logger.info(f"Retrieved {len(data)} records")
            return data

        except sqlite3.Error as e:
            logger.error(f"Failed to fetch data: {e}")
            return []

    def get_dataframe(self, **kwargs) -> pd.DataFrame:
        """
        Fetch data from the database as a pandas DataFrame.

        Example:
            df = db.get_dataframe(status='active')

        Args:
            **kwargs: Parameters for the WHERE clause

        Returns:
            pandas DataFrame with the results
        """
        # TODO: Replace with an actual hardcoded statement
        # Build WHERE clause from kwargs
        if kwargs:
            conditions = " AND ".join([f"{k} = ?" for k in kwargs.keys()])
            query = f"""
                SELECT * FROM your_table 
                WHERE {conditions}
            """
            params = tuple(kwargs.values())
        else:
            query = "SELECT * FROM your_table"
            params = None

        try:
            df = pd.read_sql_query(query, self._conn, params=params)
            logger.info(f"Retrieved {len(df)} records as DataFrame")
            return df

        except Exception as e:
            logger.error(f"Failed to fetch DataFrame: {e}")
            return pd.DataFrame()

    def insert(self, commit: bool = False, **kwargs) -> int:
        """
        Insert a record into the database.

        Example:
            user_id = db.insert(name='John', email='john@example.com', commit=True)
            # Or batch without committing:
            for data in records:
                db.insert(**data)
            db.commit()  # Commit all at once

        Args:
            commit: Whether to commit the transaction immediately
            **kwargs: Column names and values to insert

        Returns:
            ID of the inserted row
        """
        # TODO: Replace with an actual hardcoded statement
        if not kwargs:
            raise ValueError("No data provided for insert")

        columns = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?" for _ in kwargs])

        query = f"""
            INSERT INTO your_table ({columns})
            VALUES ({placeholders})
        """
        params = tuple(kwargs.values())

        try:
            cursor = self._execute(query, params)
            row_id = cursor.lastrowid

            if commit:
                self._conn.commit()
                logger.info(f"Inserted and committed record with ID: {row_id}")
            else:
                logger.info(f"Inserted record with ID: {row_id} (not committed)")

            return row_id

        except sqlite3.Error as e:
            logger.error(f"Insert failed: {e}")
            if commit:
                self._conn.rollback()
            raise

    def update(self, record_id: int, commit: bool = False, **kwargs) -> int:
        """
        Update a record in the database.

        Example:
            rows_affected = db.update(1, name='Jane', email='jane@example.com', commit=True)
            # Or batch updates:
            db.update(1, status='processing')
            db.update(2, status='processing')
            db.commit()  # Commit both at once

        Args:
            record_id: ID of the record to update
            commit: Whether to commit the transaction immediately
            **kwargs: Column names and values to update

        Returns:
            Number of rows affected
        """
        # TODO: Replace with an actual hardcoded statement
        if not kwargs:
            raise ValueError("No data provided for update")

        set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])

        query = f"""
            UPDATE your_table 
            SET {set_clause}
            WHERE id = ?
        """
        params = tuple(list(kwargs.values()) + [record_id])

        try:
            cursor = self._execute(query, params)
            rows_affected = cursor.rowcount

            if commit:
                self._conn.commit()
                logger.info(f"Updated and committed {rows_affected} record(s)")
            else:
                logger.info(f"Updated {rows_affected} record(s) (not committed)")

            return rows_affected

        except sqlite3.Error as e:
            logger.error(f"Update failed: {e}")
            if commit:
                self._conn.rollback()
            raise

    def delete(self, record_id: int, commit: bool = False) -> int:
        """
        Delete a record from the database.

        Example:
            rows_affected = db.delete(1, commit=True)
            # Or batch deletes:
            for id in old_records:
                db.delete(id)
            db.commit()  # Commit all at once

        Args:
            record_id: ID of the record to delete
            commit: Whether to commit the transaction immediately

        Returns:
            Number of rows deleted
        """
        query = "DELETE FROM your_table WHERE id = ?"
        params = (record_id,)

        try:
            cursor = self._execute(query, params)
            rows_affected = cursor.rowcount

            if commit:
                self._conn.commit()
                logger.info(f"Deleted and committed {rows_affected} record(s)")
            else:
                logger.info(f"Deleted {rows_affected} record(s) (not committed)")

            return rows_affected

        except sqlite3.Error as e:
            logger.error(f"Delete failed: {e}")
            if commit:
                self._conn.rollback()
            raise

    def commit(self):
        """Commit the current transaction."""
        if self._conn:
            try:
                self._conn.commit()
                logger.debug("Transaction committed")
            except sqlite3.Error as e:
                logger.error(f"Commit failed: {e}")
                raise

    def rollback(self):
        """Rollback the current transaction."""
        if self._conn:
            try:
                self._conn.rollback()
                logger.debug("Transaction rolled back")
            except sqlite3.Error as e:
                logger.error(f"Rollback failed: {e}")
                raise


class SingletonDatabase:
    """
    Singleton wrapper for DatabaseManager.
    Ensures only one database connection exists application-wide.

    Usage:
        db = SingletonDatabase.get_instance()
        data = db.get_data(user_id=1)
    """
    _instance: Optional[DatabaseManager] = None

    @classmethod
    def get_instance(cls, db_path: Optional[str] = None) -> DatabaseManager:
        """
        Get or create the singleton database instance.

        Args:
            db_path: Optional path to database (only used on first call)

        Returns:
            The singleton DatabaseManager instance
        """
        if cls._instance is None:
            cls._instance = DatabaseManager(db_path)
            logger.info("Created singleton database instance")
        elif db_path:
            logger.warning("Database path ignored - singleton already initialized")

        return cls._instance

    @classmethod
    def reset(cls):
        """
        Reset the singleton instance.
        Useful for testing or changing database connections.
        """
        if cls._instance:
            cls._instance.close()
            cls._instance = None
            logger.info("Singleton database instance reset")


# TODO: Put example usage into a separate main module
if __name__ == "__main__":
    # Set up logging for demo
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Example 1: Regular instance
    print("Example 1: Regular DatabaseManager")
    db = DatabaseManager("example.db")

    # Insert with immediate commit
    user_id = db.insert(name="John Doe", email="john@example.com", age=30, commit=True)
    print(f"Inserted user with ID: {user_id}")

    # Batch operations without immediate commit
    print("\nBatch operations:")
    for i in range(3):
        db.insert(name=f"User {i}", email=f"user{i}@example.com", age=20+i)
    db.commit()  # Commit all at once
    print("Batch insert completed")

    # Fetch data
    users = db.get_data(age=30)
    print(f"Found {len(users)} users with age 30")

    # Update with commit
    db.update(user_id, email="newemail@example.com", commit=True)

    # Get as DataFrame
    df = db.get_dataframe()
    print(f"DataFrame shape: {df.shape}")

    # Delete with commit
    db.delete(user_id, commit=True)

    # Close connection
    db.close()

    print("\nExample 2: Singleton pattern")
    # Example 2: Singleton pattern
    db1 = SingletonDatabase.get_instance("singleton.db")
    db2 = SingletonDatabase.get_instance()  # Returns same instance

    print(f"Same instance? {db1 is db2}")  # True

    # Use the singleton with commit parameter
    db1.insert(name="Jane Doe", email="jane@example.com", commit=True)

    # Batch operations through singleton
    db2.insert(name="Bob Smith", email="bob@example.com")
    db2.insert(name="Alice Jones", email="alice@example.com")
    db2.commit()  # Commit both operations

    data = db2.get_data()  # Same database
    print(f"Total records: {len(data)}")

    # Reset singleton when done
    SingletonDatabase.reset()

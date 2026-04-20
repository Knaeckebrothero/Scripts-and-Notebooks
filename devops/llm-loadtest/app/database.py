"""SQLite database for storing load test metrics."""

import sqlite3
import threading
import os
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass
import pandas as pd


@dataclass
class MetricRecord:
    """A single metric record from a request."""
    endpoint: str
    model_name: str
    streaming: bool
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    time_to_first_token_ms: Optional[float]
    total_response_time_ms: float
    status: str  # 'success', 'error', 'timeout'
    error_message: Optional[str] = None
    http_status: Optional[int] = None
    timestamp: Optional[datetime] = None


class MetricsDatabase:
    """Thread-safe SQLite database for metrics storage."""

    _instance: Optional["MetricsDatabase"] = None
    _lock = threading.Lock()

    def __init__(self, db_path: str = "data/metrics.db"):
        self.db_path = db_path
        self._local = threading.local()

        # Ensure directory exists
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)

        # Initialize schema
        self._init_schema()

    @classmethod
    def get_instance(cls, db_path: str = "data/metrics.db") -> "MetricsDatabase":
        """Get singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db_path)
        return cls._instance

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, "connection") or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self.db_path,
                timeout=30.0,
                check_same_thread=False,
            )
            # Enable WAL mode for better concurrent performance
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA synchronous=NORMAL")
            self._local.connection.row_factory = sqlite3.Row
        return self._local.connection

    def _init_schema(self):
        """Initialize database schema."""
        conn = self._get_connection()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                endpoint TEXT NOT NULL,
                model_name TEXT NOT NULL,
                streaming INTEGER NOT NULL,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                time_to_first_token_ms REAL,
                total_response_time_ms REAL NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                http_status INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp);
            CREATE INDEX IF NOT EXISTS idx_metrics_endpoint ON metrics(endpoint);
            CREATE INDEX IF NOT EXISTS idx_metrics_status ON metrics(status);

            CREATE TABLE IF NOT EXISTS test_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time DATETIME NOT NULL,
                end_time DATETIME,
                config_json TEXT,
                status TEXT DEFAULT 'running'
            );
        """)
        conn.commit()

    def record_metric(self, metric: MetricRecord, commit: bool = True):
        """Record a single metric."""
        conn = self._get_connection()
        conn.execute("""
            INSERT INTO metrics (
                timestamp, endpoint, model_name, streaming,
                prompt_tokens, completion_tokens, time_to_first_token_ms,
                total_response_time_ms, status, error_message, http_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            metric.timestamp or datetime.now(),
            metric.endpoint,
            metric.model_name,
            1 if metric.streaming else 0,
            metric.prompt_tokens,
            metric.completion_tokens,
            metric.time_to_first_token_ms,
            metric.total_response_time_ms,
            metric.status,
            metric.error_message,
            metric.http_status,
        ))
        if commit:
            conn.commit()

    def record_metrics_batch(self, metrics: list[MetricRecord]):
        """Record multiple metrics in a batch."""
        conn = self._get_connection()
        for metric in metrics:
            self.record_metric(metric, commit=False)
        conn.commit()

    def get_stats(self, window_minutes: int = 15) -> dict:
        """Get aggregated statistics for the last N minutes."""
        conn = self._get_connection()
        cutoff = datetime.now() - timedelta(minutes=window_minutes)

        cursor = conn.execute("""
            SELECT
                COUNT(*) as total_requests,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
                SUM(CASE WHEN status = 'timeout' THEN 1 ELSE 0 END) as timeouts,
                AVG(total_response_time_ms) as avg_response_time,
                AVG(time_to_first_token_ms) as avg_ttft,
                SUM(prompt_tokens) as total_prompt_tokens,
                SUM(completion_tokens) as total_completion_tokens
            FROM metrics
            WHERE timestamp >= ?
        """, (cutoff,))

        row = cursor.fetchone()
        return {
            "total_requests": row["total_requests"] or 0,
            "successful": row["successful"] or 0,
            "errors": row["errors"] or 0,
            "timeouts": row["timeouts"] or 0,
            "avg_response_time_ms": row["avg_response_time"] or 0,
            "avg_ttft_ms": row["avg_ttft"] or 0,
            "total_prompt_tokens": row["total_prompt_tokens"] or 0,
            "total_completion_tokens": row["total_completion_tokens"] or 0,
            "window_minutes": window_minutes,
        }

    def get_percentiles(self, window_minutes: int = 15, sample_size: int = 10000) -> dict:
        """Get response time percentiles.

        Uses sampling for large datasets to avoid memory issues.
        For a 72-hour test at 300 req/s, this could be ~77M records.
        """
        conn = self._get_connection()
        cutoff = datetime.now() - timedelta(minutes=window_minutes)

        # First, get the count
        count_cursor = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM metrics
            WHERE timestamp >= ? AND status = 'success'
        """, (cutoff,))
        total_count = count_cursor.fetchone()["cnt"]

        if total_count == 0:
            return {"p50": 0, "p90": 0, "p95": 0, "p99": 0}

        # Use sampling if dataset is large
        if total_count > sample_size:
            # Random sampling using SQLite's random()
            cursor = conn.execute("""
                SELECT total_response_time_ms
                FROM metrics
                WHERE timestamp >= ? AND status = 'success'
                ORDER BY RANDOM()
                LIMIT ?
            """, (cutoff, sample_size))
        else:
            cursor = conn.execute("""
                SELECT total_response_time_ms
                FROM metrics
                WHERE timestamp >= ? AND status = 'success'
            """, (cutoff,))

        times = sorted([row["total_response_time_ms"] for row in cursor.fetchall()])

        if not times:
            return {"p50": 0, "p90": 0, "p95": 0, "p99": 0}

        def percentile(data, p):
            idx = int(len(data) * p / 100)
            return data[min(idx, len(data) - 1)]

        return {
            "p50": percentile(times, 50),
            "p90": percentile(times, 90),
            "p95": percentile(times, 95),
            "p99": percentile(times, 99),
        }

    def get_stats_by_endpoint(self, window_minutes: int = 15) -> dict:
        """Get statistics grouped by endpoint."""
        conn = self._get_connection()
        cutoff = datetime.now() - timedelta(minutes=window_minutes)

        cursor = conn.execute("""
            SELECT
                endpoint,
                COUNT(*) as total_requests,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
                AVG(total_response_time_ms) as avg_response_time,
                AVG(time_to_first_token_ms) as avg_ttft
            FROM metrics
            WHERE timestamp >= ?
            GROUP BY endpoint
        """, (cutoff,))

        results = {}
        for row in cursor.fetchall():
            results[row["endpoint"]] = {
                "total_requests": row["total_requests"],
                "successful": row["successful"],
                "errors": row["errors"],
                "avg_response_time_ms": row["avg_response_time"] or 0,
                "avg_ttft_ms": row["avg_ttft"] or 0,
            }
        return results

    def get_recent_metrics(self, limit: int = 100) -> list[dict]:
        """Get most recent metrics."""
        conn = self._get_connection()
        cursor = conn.execute("""
            SELECT * FROM metrics
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def get_timeseries(self, window_minutes: int = 60, bucket_seconds: int = 60) -> pd.DataFrame:
        """Get time series data for charts."""
        conn = self._get_connection()
        cutoff = datetime.now() - timedelta(minutes=window_minutes)

        query = f"""
            SELECT
                strftime('%Y-%m-%d %H:%M:00', timestamp) as bucket,
                endpoint,
                COUNT(*) as requests,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
                AVG(total_response_time_ms) as avg_response_time
            FROM metrics
            WHERE timestamp >= ?
            GROUP BY bucket, endpoint
            ORDER BY bucket
        """

        return pd.read_sql_query(query, conn, params=(cutoff,))

    def get_total_stats(self) -> dict:
        """Get all-time statistics."""
        conn = self._get_connection()
        cursor = conn.execute("""
            SELECT
                COUNT(*) as total_requests,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
                MIN(timestamp) as first_request,
                MAX(timestamp) as last_request
            FROM metrics
        """)
        row = cursor.fetchone()
        return {
            "total_requests": row["total_requests"] or 0,
            "successful": row["successful"] or 0,
            "errors": row["errors"] or 0,
            "first_request": row["first_request"],
            "last_request": row["last_request"],
        }

    def export_csv(self, filepath: str, start_time: Optional[datetime] = None):
        """Export metrics to CSV file."""
        conn = self._get_connection()

        query = "SELECT * FROM metrics"
        params = ()

        if start_time:
            query += " WHERE timestamp >= ?"
            params = (start_time,)

        query += " ORDER BY timestamp"

        df = pd.read_sql_query(query, conn, params=params)
        df.to_csv(filepath, index=False)
        return len(df)

    def clear_metrics(self):
        """Clear all metrics (use with caution)."""
        conn = self._get_connection()
        conn.execute("DELETE FROM metrics")
        conn.commit()

    def close(self):
        """Close the database connection."""
        if hasattr(self._local, "connection") and self._local.connection:
            self._local.connection.close()
            self._local.connection = None

"""Streamlit dashboard for LLM load testing."""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import threading
import asyncio
import time
import os
import logging
from datetime import datetime, timedelta

from config import LoadTestConfig, EndpointConfig
from database import MetricsDatabase
from loadtester import LoadTester

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Page config
st.set_page_config(
    page_title="LLM Load Tester",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS
st.markdown("""
<style>
    .metric-card {
        background-color: #1e1e1e;
        border-radius: 10px;
        padding: 15px;
        margin: 5px 0;
    }
    .status-running { color: #00ff00; font-weight: bold; }
    .status-paused { color: #ffaa00; font-weight: bold; }
    .status-stopped { color: #888888; }
</style>
""", unsafe_allow_html=True)


class LoadTestRunner:
    """Manages the load test in a background thread."""

    def __init__(self):
        self.tester: LoadTester = None
        self.thread: threading.Thread = None
        self.loop: asyncio.AbstractEventLoop = None
        self._lock = threading.Lock()

    def initialize(self, config: LoadTestConfig):
        """Initialize the load tester with config."""
        with self._lock:
            if self.tester is None:
                self.tester = LoadTester(config=config)

    def start(self):
        """Start the load test in a background thread."""
        with self._lock:
            if self.tester is None:
                return False

            if self.tester.state.running:
                return False

            def run_loop():
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                try:
                    self.loop.run_until_complete(self.tester.start())
                except Exception as e:
                    logger.exception(f"Error in load test loop: {e}")
                finally:
                    self.loop.close()
                    self.loop = None

            self.thread = threading.Thread(target=run_loop, daemon=True)
            self.thread.start()

            # Wait a bit for the loop to start
            time.sleep(0.3)
            return True

    def stop(self):
        """Stop the load test."""
        with self._lock:
            if self.tester:
                self.tester.stop()

    def pause(self):
        """Pause the load test."""
        with self._lock:
            if self.tester:
                self.tester.pause()

    def resume(self):
        """Resume the load test."""
        with self._lock:
            if self.tester:
                self.tester.resume()

    def get_status(self) -> dict:
        """Get current status."""
        with self._lock:
            if self.tester:
                return self.tester.get_status()
            return {
                "running": False,
                "paused": False,
                "elapsed_seconds": 0,
                "total_requests": 0,
                "total_errors": 0,
                "endpoints": [],
                "streaming_ratio": 0.7,
            }

    def update_endpoint_rate(self, name: str, rate: float):
        """Update endpoint rate."""
        with self._lock:
            if self.tester:
                self.tester.update_endpoint_rate(name, rate)

    def enable_endpoint(self, name: str, enabled: bool):
        """Enable/disable endpoint."""
        with self._lock:
            if self.tester:
                self.tester.enable_endpoint(name, enabled)

    def update_streaming_ratio(self, ratio: float):
        """Update streaming ratio."""
        with self._lock:
            if self.tester:
                self.tester.update_streaming_ratio(ratio)


def get_runner() -> LoadTestRunner:
    """Get or create the global load test runner."""
    if "runner" not in st.session_state:
        st.session_state.runner = LoadTestRunner()
        config = LoadTestConfig.default_university()
        st.session_state.runner.initialize(config)
    return st.session_state.runner


def get_db() -> MetricsDatabase:
    """Get database instance."""
    if "db" not in st.session_state:
        config = LoadTestConfig.default_university()
        db_path = config.db_path
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        st.session_state.db = MetricsDatabase.get_instance(db_path)
    return st.session_state.db


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"


def format_number(n: int) -> str:
    """Format large numbers with K/M suffix."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def render_control_panel():
    """Render the control panel tab."""
    runner = get_runner()
    status = runner.get_status()

    st.subheader("Test Controls")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if not status["running"]:
            if st.button("Start", type="primary", use_container_width=True):
                runner.start()
                time.sleep(0.5)
                st.rerun()
        else:
            if st.button("Stop", type="secondary", use_container_width=True):
                runner.stop()
                time.sleep(0.5)
                st.rerun()

    with col2:
        if status["running"] and not status["paused"]:
            if st.button("Pause", use_container_width=True):
                runner.pause()
                st.rerun()
        elif status["running"] and status["paused"]:
            if st.button("Resume", type="primary", use_container_width=True):
                runner.resume()
                st.rerun()
        else:
            st.button("Pause", use_container_width=True, disabled=True)

    with col3:
        status_text = "Stopped"
        status_class = "status-stopped"
        if status["running"]:
            if status["paused"]:
                status_text = "Paused"
                status_class = "status-paused"
            else:
                status_text = "Running"
                status_class = "status-running"
        st.markdown(f"**Status:** <span class='{status_class}'>{status_text}</span>", unsafe_allow_html=True)

    with col4:
        st.metric("Elapsed", format_duration(status["elapsed_seconds"]))

    # Quick stats
    if status["running"]:
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Requests", format_number(status["total_requests"]))
        with col2:
            error_rate = 0
            if status["total_requests"] > 0:
                error_rate = (status["total_errors"] / status["total_requests"]) * 100
            st.metric("Error Rate", f"{error_rate:.1f}%")

    st.divider()

    # Global settings
    st.subheader("Global Settings")

    streaming_ratio = st.slider(
        "Streaming Ratio",
        min_value=0.0,
        max_value=1.0,
        value=status["streaming_ratio"],
        step=0.05,
        format="%.0f%%",
        help="Percentage of requests that use streaming",
    )
    if abs(streaming_ratio - status["streaming_ratio"]) > 0.01:
        runner.update_streaming_ratio(streaming_ratio)

    st.divider()

    # Per-endpoint settings
    st.subheader("Endpoint Configuration")

    for endpoint in status["endpoints"]:
        with st.expander(f"{endpoint['name']} - {endpoint['url']}", expanded=True):
            col1, col2, col3 = st.columns([1, 2, 1])

            with col1:
                enabled = st.checkbox(
                    "Enabled",
                    value=endpoint["enabled"],
                    key=f"enabled_{endpoint['name']}",
                )
                if enabled != endpoint["enabled"]:
                    runner.enable_endpoint(endpoint["name"], enabled)

            with col2:
                rate = st.slider(
                    "Requests/second",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(endpoint["requests_per_second"]),
                    step=5.0,
                    key=f"rate_{endpoint['name']}",
                )
                if abs(rate - endpoint["requests_per_second"]) > 0.1:
                    runner.update_endpoint_rate(endpoint["name"], rate)

            with col3:
                if endpoint["enabled"]:
                    st.success(f"{rate:.0f} req/s")
                else:
                    st.warning("Disabled")


def render_live_metrics():
    """Render the live metrics tab."""
    db = get_db()
    runner = get_runner()
    status = runner.get_status()

    # Auto-refresh control
    col1, col2 = st.columns([3, 1])
    with col2:
        auto_refresh = st.checkbox("Auto-refresh (15s)", value=status["running"])

    # Summary metrics
    st.subheader("Current Performance (Last 15 min)")

    stats = db.get_stats(window_minutes=15)
    percentiles = db.get_percentiles(window_minutes=15)

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        req_per_sec = 0
        if stats["window_minutes"] > 0 and stats["total_requests"] > 0:
            req_per_sec = stats["total_requests"] / (stats["window_minutes"] * 60)
        st.metric(
            "Requests",
            format_number(stats["total_requests"]),
            delta=f"{req_per_sec:.1f}/s" if req_per_sec > 0 else None,
        )

    with col2:
        success_rate = 0
        if stats["total_requests"] > 0:
            success_rate = (stats["successful"] / stats["total_requests"]) * 100
        st.metric("Success Rate", f"{success_rate:.1f}%")

    with col3:
        st.metric("Avg Response", f"{stats['avg_response_time_ms']:.0f}ms")

    with col4:
        ttft = stats["avg_ttft_ms"]
        st.metric("Avg TTFT", f"{ttft:.0f}ms" if ttft else "N/A")

    # Percentiles
    st.subheader("Response Time Percentiles")
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("P50", f"{percentiles['p50']:.0f}ms")
    with col2:
        st.metric("P90", f"{percentiles['p90']:.0f}ms")
    with col3:
        st.metric("P95", f"{percentiles['p95']:.0f}ms")
    with col4:
        st.metric("P99", f"{percentiles['p99']:.0f}ms")

    # Per-endpoint stats
    st.subheader("Per-Endpoint Performance")

    endpoint_stats = db.get_stats_by_endpoint(window_minutes=15)

    if endpoint_stats:
        cols = st.columns(len(endpoint_stats))
        for i, (endpoint, ep_stats) in enumerate(endpoint_stats.items()):
            with cols[i]:
                st.markdown(f"**{endpoint}**")
                st.metric("Requests", format_number(ep_stats["total_requests"]))
                st.metric("Avg Response", f"{ep_stats['avg_response_time_ms']:.0f}ms")
                error_pct = 0
                if ep_stats["total_requests"] > 0:
                    error_pct = (ep_stats["errors"] / ep_stats["total_requests"]) * 100
                st.metric("Error Rate", f"{error_pct:.1f}%")
    else:
        st.info("No data yet. Start the load test to see metrics.")

    # Time series chart
    st.subheader("Request Rate Over Time")

    timeseries = db.get_timeseries(window_minutes=60)

    if not timeseries.empty:
        fig = px.line(
            timeseries,
            x="bucket",
            y="requests",
            color="endpoint",
            title="Requests per Minute by Endpoint",
            markers=True,
        )
        fig.update_layout(
            xaxis_title="Time",
            yaxis_title="Requests",
            legend_title="Endpoint",
            hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True)

        # Response time chart
        fig2 = px.line(
            timeseries,
            x="bucket",
            y="avg_response_time",
            color="endpoint",
            title="Average Response Time by Endpoint",
            markers=True,
        )
        fig2.update_layout(
            xaxis_title="Time",
            yaxis_title="Response Time (ms)",
            legend_title="Endpoint",
            hovermode="x unified",
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No time series data available yet.")

    # Auto-refresh every 15 seconds
    if auto_refresh and status["running"]:
        time.sleep(15)
        st.rerun()


def render_analysis():
    """Render the analysis tab."""
    db = get_db()

    st.subheader("Analysis & Export")

    # Time range selector
    time_range = st.selectbox(
        "Time Range",
        options=[15, 60, 360, 1440, 4320],
        index=1,
        format_func=lambda x: {
            15: "Last 15 minutes",
            60: "Last hour",
            360: "Last 6 hours",
            1440: "Last 24 hours",
            4320: "Last 3 days",
        }.get(x, f"Last {x} minutes"),
    )

    # Overall stats
    total_stats = db.get_total_stats()

    st.subheader("All-Time Statistics")
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Requests", format_number(total_stats["total_requests"]))
    with col2:
        st.metric("Successful", format_number(total_stats["successful"]))
    with col3:
        st.metric("Errors", format_number(total_stats["errors"]))
    with col4:
        if total_stats["total_requests"] > 0:
            success_rate = (total_stats["successful"] / total_stats["total_requests"]) * 100
            st.metric("Success Rate", f"{success_rate:.2f}%")

    if total_stats["first_request"] and total_stats["last_request"]:
        st.caption(f"Data from {total_stats['first_request']} to {total_stats['last_request']}")

    # Response time distribution
    st.subheader("Response Time Distribution")

    stats = db.get_stats(window_minutes=time_range)

    if stats["total_requests"] > 0:
        recent = db.get_recent_metrics(limit=5000)
        if recent:
            df = pd.DataFrame(recent)
            df_success = df[df["status"] == "success"]

            if not df_success.empty:
                fig = px.histogram(
                    df_success,
                    x="total_response_time_ms",
                    nbins=50,
                    title="Response Time Distribution",
                    color="endpoint",
                    barmode="overlay",
                    opacity=0.7,
                )
                fig.update_layout(
                    xaxis_title="Response Time (ms)",
                    yaxis_title="Count",
                )
                st.plotly_chart(fig, use_container_width=True)

                # Streaming vs non-streaming comparison
                st.subheader("Streaming vs Non-Streaming")

                streaming_data = []
                for streaming_val in [0, 1]:
                    subset = df_success[df_success["streaming"] == streaming_val]
                    if not subset.empty:
                        streaming_data.append({
                            "Type": "Streaming" if streaming_val else "Non-Streaming",
                            "Count": len(subset),
                            "Avg Response (ms)": subset["total_response_time_ms"].mean(),
                            "Median Response (ms)": subset["total_response_time_ms"].median(),
                            "Avg TTFT (ms)": subset["time_to_first_token_ms"].mean() if streaming_val else None,
                        })

                if streaming_data:
                    st.dataframe(pd.DataFrame(streaming_data), use_container_width=True)

    # Error analysis
    st.subheader("Error Analysis")
    recent = db.get_recent_metrics(limit=1000)
    if recent:
        df = pd.DataFrame(recent)
        errors = df[df["status"] != "success"]

        if not errors.empty:
            error_counts = errors.groupby(["endpoint", "status"]).size().reset_index(name="count")
            fig = px.bar(
                error_counts,
                x="endpoint",
                y="count",
                color="status",
                title="Errors by Endpoint and Type",
                barmode="group",
            )
            st.plotly_chart(fig, use_container_width=True)

            # Show recent errors
            st.subheader("Recent Errors")
            error_df = errors[["timestamp", "endpoint", "status", "error_message", "http_status"]].head(20)
            st.dataframe(error_df, use_container_width=True)
        else:
            st.success("No errors recorded!")
    else:
        st.info("No data available.")

    # Export section
    st.divider()
    st.subheader("Data Management")

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Export to CSV", type="primary", use_container_width=True):
            export_path = f"data/export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            os.makedirs("data", exist_ok=True)
            count = db.export_csv(export_path)
            st.success(f"Exported {count:,} records to {export_path}")

    with col2:
        if st.button("Refresh Data", use_container_width=True):
            st.rerun()

    with col3:
        if st.button("Clear All Metrics", type="secondary", use_container_width=True):
            if st.session_state.get("confirm_clear"):
                db.clear_metrics()
                st.session_state.confirm_clear = False
                st.success("Metrics cleared!")
                st.rerun()
            else:
                st.session_state.confirm_clear = True
                st.warning("Click again to confirm")


def main():
    """Main application."""
    st.title("LLM Load Tester")
    st.caption("Load testing tool for OpenAI-compatible LLM endpoints")

    # Initialize
    get_runner()
    get_db()

    # Tabs
    tab1, tab2, tab3 = st.tabs(["Control Panel", "Live Metrics", "Analysis"])

    with tab1:
        render_control_panel()

    with tab2:
        render_live_metrics()

    with tab3:
        render_analysis()


if __name__ == "__main__":
    main()

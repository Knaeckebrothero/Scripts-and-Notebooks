"""Prometheus metrics, on a registry of their own (no process-global state).

Served without auth on the internal port only. Gauges that mirror router state
(backend up/down, keys loaded, queue depth) are read at scrape time by a
collector, so they never go stale after a config reload.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    disable_created_metrics,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.exposition import CONTENT_TYPE_LATEST
from prometheus_client.registry import Collector

# Chat requests may legitimately run for the full 600 s read timeout.
_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600)
_PROBE_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)

REJECTION_REASONS = (
    "auth",
    "unknown_model",
    "wrong_endpoint",
    "forbidden",
    "rate",
    "concurrency",
    "unavailable",
    "invalid_request",
    "body_too_large",
    "not_found",
    "busy",
    "shutting_down",
)

StateSamples = Callable[[], Iterable[tuple[str, str, list[str], list[tuple[list[str], float]]]]]


class _StateCollector(Collector):
    def __init__(self, source: StateSamples) -> None:
        self._source = source

    def collect(self) -> Iterable[Metric]:
        for name, doc, label_names, samples in self._source():
            family = GaugeMetricFamily(name, doc, labels=label_names)
            for label_values, value in samples:
                family.add_metric(label_values, value)
            yield family


class Metrics:
    content_type = CONTENT_TYPE_LATEST

    def __init__(self, *, version: str, git_sha: str) -> None:
        # The *_created series double the number of series and are not used.
        disable_created_metrics()  # type: ignore[no-untyped-call]
        self.registry = CollectorRegistry()
        reg = self.registry
        self.requests = Counter(
            "router_requests",
            "Requests that reached a known key and model, by outcome",
            ["route", "model", "key_id", "key_name", "outcome"],
            registry=reg,
        )
        self.tokens = Counter(
            "router_tokens",
            "Tokens reported by backends",
            ["route", "model", "key_id", "key_name", "kind"],
            registry=reg,
        )
        self.duration = Histogram(
            "router_request_duration_seconds",
            "Time from request arrival to the end of the response, forwarded requests",
            ["route"],
            buckets=_BUCKETS,
            registry=reg,
        )
        self.ttfb = Histogram(
            "router_time_to_first_byte_seconds",
            "Time from request arrival to the first response body byte, forwarded requests",
            ["route"],
            buckets=_BUCKETS,
            registry=reg,
        )
        self.in_flight = Gauge(
            "router_in_flight_requests", "Requests currently being forwarded", ["route"], registry=reg
        )
        self.rejections = Counter(
            "router_rejections", "Requests the router answered without forwarding", ["reason"], registry=reg
        )
        self.backend_failures = Counter(
            "router_backend_failures",
            "Failed attempts against a backend",
            ["route", "backend", "kind"],
            registry=reg,
        )
        self.probe_duration = Histogram(
            "router_probe_duration_seconds",
            "Duration of active health probes",
            ["backend", "result"],
            buckets=_PROBE_BUCKETS,
            registry=reg,
        )
        self.usage_dropped = Counter(
            "router_usage_records_dropped", "Usage records lost (queue full or database errors)", registry=reg
        )
        self.usage_write_failures = Counter(
            "router_usage_write_failures", "Failed attempts to write a usage batch", registry=reg
        )
        self.key_refresh_failures = Counter(
            "router_key_refresh_failures", "Failed refreshes of the key cache", registry=reg
        )
        self.config_reloads = Counter(
            "router_config_reloads", "Config reload attempts by result", ["result"], registry=reg
        )
        self.internal_errors = Counter(
            "router_internal_errors", "Unexpected errors inside the router", registry=reg
        )
        for reason in REJECTION_REASONS:
            self.rejections.labels(reason)
        for result in ("ok", "error"):
            self.config_reloads.labels(result)
        build = Gauge(
            "router_build_info", "Version and git commit of this build", ["version", "git_sha"], registry=reg
        )
        build.labels(version, git_sha).set(1)

    def add_state_source(self, source: StateSamples) -> None:
        self.registry.register(_StateCollector(source))

    def render(self) -> bytes:
        return generate_latest(self.registry)

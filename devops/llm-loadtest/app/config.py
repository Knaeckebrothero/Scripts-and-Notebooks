"""Configuration dataclasses for the LLM load testing tool."""

from dataclasses import dataclass, field
from typing import Optional
import os
import json


@dataclass
class EndpointConfig:
    """Configuration for a single LLM endpoint."""
    name: str
    url: str
    model: str = "default"
    requests_per_second: float = 50.0
    enabled: bool = True

    def __post_init__(self):
        # Ensure URL doesn't have trailing slash
        self.url = self.url.rstrip("/")


@dataclass
class LoadTestConfig:
    """Main configuration for the load test."""
    endpoints: list[EndpointConfig] = field(default_factory=list)
    streaming_ratio: float = 0.7  # 70% streaming, 30% non-streaming
    duration_hours: float = 72.0
    request_timeout: float = 120.0  # seconds
    max_concurrent_requests: int = 500
    db_path: str = "/app/data/metrics.db"
    prompts_file: str = "/app/data/prompts.json"

    @classmethod
    def from_env(cls) -> "LoadTestConfig":
        """Load configuration from environment variables."""
        # Parse endpoints from ENDPOINTS env var
        # Format: "name1:url1,name2:url2,..."
        endpoints_str = os.getenv("ENDPOINTS", "")
        endpoints = []

        if endpoints_str:
            for endpoint_def in endpoints_str.split(","):
                parts = endpoint_def.strip().split(":")
                if len(parts) >= 2:
                    name = parts[0]
                    # Handle URLs with colons (http://...)
                    url = ":".join(parts[1:])
                    endpoints.append(EndpointConfig(
                        name=name,
                        url=url,
                        model=name,
                        requests_per_second=float(os.getenv("REQUESTS_PER_SECOND", "50")),
                    ))

        return cls(
            endpoints=endpoints,
            streaming_ratio=float(os.getenv("STREAMING_RATIO", "0.7")),
            duration_hours=float(os.getenv("DURATION_HOURS", "72")),
            request_timeout=float(os.getenv("REQUEST_TIMEOUT", "120")),
            max_concurrent_requests=int(os.getenv("MAX_CONCURRENT_REQUESTS", "500")),
            db_path=os.getenv("DB_PATH", "/app/data/metrics.db"),
            prompts_file=os.getenv("PROMPTS_FILE", "/app/data/prompts.json"),
        )

    @classmethod
    def default_university(cls) -> "LoadTestConfig":
        """Default configuration for university LLM servers."""
        return cls(
            endpoints=[
                EndpointConfig(
                    name="llama-3.1-8b",
                    url="http://10.18.2.105:9000",
                    model="llama-3.1-8b",
                    requests_per_second=50.0,
                ),
                EndpointConfig(
                    name="phi3-14b",
                    url="http://10.18.2.105:9001",
                    model="phi3-14b",
                    requests_per_second=50.0,
                ),
                EndpointConfig(
                    name="oss-20b",
                    url="http://10.18.2.105:9002",
                    model="oss-20b",
                    requests_per_second=50.0,
                ),
            ],
            streaming_ratio=0.7,
            duration_hours=72.0,
        )


@dataclass
class TestState:
    """Runtime state of the load test."""
    running: bool = False
    paused: bool = False
    start_time: Optional[float] = None
    total_requests_sent: int = 0
    total_errors: int = 0


# Default prompts if no dataset is loaded
DEFAULT_PROMPTS = [
    "Explain the concept of machine learning in simple terms.",
    "What are the benefits of renewable energy?",
    "Write a short story about a robot learning to paint.",
    "How does photosynthesis work?",
    "What are the main differences between Python and JavaScript?",
    "Describe the water cycle.",
    "What is the theory of relativity?",
    "How do neural networks learn?",
    "Explain blockchain technology.",
    "What causes seasons on Earth?",
]

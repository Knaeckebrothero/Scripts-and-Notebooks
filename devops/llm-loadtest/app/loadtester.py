"""Async load test engine using aiohttp and Poisson-distributed requests."""

import asyncio
import aiohttp
import random
import time
import json
import logging
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

from config import EndpointConfig, LoadTestConfig, TestState, DEFAULT_PROMPTS
from database import MetricsDatabase, MetricRecord

logger = logging.getLogger(__name__)


@dataclass
class LoadTester:
    """Async load tester with Poisson-distributed request scheduling."""

    config: LoadTestConfig
    db: Optional[MetricsDatabase] = None
    state: TestState = field(default_factory=TestState)
    prompts: list[str] = field(default_factory=list)

    # These are initialized in __post_init__ to avoid event loop issues
    _tasks: list[asyncio.Task] = field(default_factory=list, repr=False)
    _session: Optional[aiohttp.ClientSession] = field(default=None, repr=False)
    _stop_event: Optional[asyncio.Event] = field(default=None, repr=False)
    _pause_event: Optional[asyncio.Event] = field(default=None, repr=False)
    _semaphore: Optional[asyncio.Semaphore] = field(default=None, repr=False)
    _initialized: bool = field(default=False, repr=False)

    def __post_init__(self):
        if self.db is None:
            self.db = MetricsDatabase.get_instance()
        self._load_prompts()

    def _ensure_initialized(self):
        """Initialize asyncio primitives in the current event loop."""
        if not self._initialized:
            self._stop_event = asyncio.Event()
            self._pause_event = asyncio.Event()
            self._pause_event.set()  # Not paused by default
            self._semaphore = asyncio.Semaphore(self.config.max_concurrent_requests)
            self._initialized = True

    def _load_prompts(self):
        """Load prompts from file or use defaults."""
        try:
            with open(self.config.prompts_file, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    self.prompts = [
                        p["content"] if isinstance(p, dict) else str(p)
                        for p in data
                    ]
                    logger.info(f"Loaded {len(self.prompts)} prompts from {self.config.prompts_file}")
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning(f"Could not load prompts file: {e}. Using defaults.")
            self.prompts = DEFAULT_PROMPTS

        if not self.prompts:
            self.prompts = DEFAULT_PROMPTS

    def _get_random_prompt(self) -> str:
        """Get a random prompt from the loaded prompts."""
        return random.choice(self.prompts)

    def _should_stream(self) -> bool:
        """Determine if this request should use streaming."""
        return random.random() < self.config.streaming_ratio

    async def _send_request(
        self,
        endpoint: EndpointConfig,
        prompt: str,
        streaming: bool,
    ) -> MetricRecord:
        """Send a single request to an endpoint and record metrics."""
        start_time = time.perf_counter()
        time_to_first_token: Optional[float] = None
        prompt_tokens: Optional[int] = None
        completion_tokens: Optional[int] = None
        status = "success"
        error_message: Optional[str] = None
        http_status: Optional[int] = None

        payload = {
            "model": endpoint.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": streaming,
            "max_tokens": 256,
        }

        try:
            url = f"{endpoint.url}/v1/chat/completions"
            timeout = aiohttp.ClientTimeout(total=self.config.request_timeout)

            async with self._semaphore:
                async with self._session.post(url, json=payload, timeout=timeout) as resp:
                    http_status = resp.status

                    if resp.status != 200:
                        status = "error"
                        error_message = f"HTTP {resp.status}: {await resp.text()}"
                    elif streaming:
                        # Handle SSE streaming response
                        first_chunk = True
                        async for line in resp.content:
                            if first_chunk:
                                time_to_first_token = (time.perf_counter() - start_time) * 1000
                                first_chunk = False
                            # Process SSE data
                            line_str = line.decode("utf-8").strip()
                            if line_str.startswith("data: ") and line_str != "data: [DONE]":
                                try:
                                    chunk = json.loads(line_str[6:])
                                    if "usage" in chunk:
                                        prompt_tokens = chunk["usage"].get("prompt_tokens")
                                        completion_tokens = chunk["usage"].get("completion_tokens")
                                except json.JSONDecodeError:
                                    pass
                    else:
                        # Non-streaming response
                        result = await resp.json()
                        if "usage" in result:
                            prompt_tokens = result["usage"].get("prompt_tokens")
                            completion_tokens = result["usage"].get("completion_tokens")

        except asyncio.TimeoutError:
            status = "timeout"
            error_message = f"Request timed out after {self.config.request_timeout}s"
        except aiohttp.ClientError as e:
            status = "error"
            error_message = str(e)
        except Exception as e:
            status = "error"
            error_message = f"Unexpected error: {e}"
            logger.exception(f"Unexpected error during request to {endpoint.name}")

        total_time = (time.perf_counter() - start_time) * 1000

        return MetricRecord(
            endpoint=endpoint.name,
            model_name=endpoint.model,
            streaming=streaming,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            time_to_first_token_ms=time_to_first_token,
            total_response_time_ms=total_time,
            status=status,
            error_message=error_message,
            http_status=http_status,
            timestamp=datetime.now(),
        )

    async def _fire_request(self, endpoint: EndpointConfig):
        """Fire a single request and record the result (non-blocking)."""
        try:
            prompt = self._get_random_prompt()
            streaming = self._should_stream()

            metric = await self._send_request(endpoint, prompt, streaming)

            # Record to database
            self.db.record_metric(metric)

            # Update state
            self.state.total_requests_sent += 1
            if metric.status != "success":
                self.state.total_errors += 1

        except Exception as e:
            logger.error(f"Error firing request to {endpoint.name}: {e}")
            self.state.total_errors += 1

    async def _endpoint_scheduler(self, endpoint: EndpointConfig):
        """Schedule requests to an endpoint using Poisson process.

        Fires requests asynchronously without waiting for completion.
        """
        logger.info(f"Starting scheduler for {endpoint.name} at {endpoint.requests_per_second} req/s")

        while not self._stop_event.is_set():
            # Check if paused
            await self._pause_event.wait()

            if not endpoint.enabled:
                await asyncio.sleep(0.1)
                continue

            rate = endpoint.requests_per_second
            if rate <= 0:
                await asyncio.sleep(0.1)
                continue

            # Poisson-distributed inter-arrival time
            delay = random.expovariate(rate)

            # Fire request without waiting (creates a background task)
            asyncio.create_task(self._fire_request(endpoint))

            # Wait for next request timing
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=delay,
                )
                # If we get here, stop was signaled
                break
            except asyncio.TimeoutError:
                pass  # Normal timeout, continue to next request

        logger.info(f"Scheduler for {endpoint.name} stopped")

    async def _run(self):
        """Main run loop - keeps running until stopped."""
        self._ensure_initialized()

        logger.info("Starting load test")
        self.state.running = True
        self.state.paused = False
        self.state.start_time = time.time()
        self._stop_event.clear()
        self._pause_event.set()

        # Create aiohttp session
        connector = aiohttp.TCPConnector(
            limit=self.config.max_concurrent_requests,
            limit_per_host=self.config.max_concurrent_requests,
        )
        self._session = aiohttp.ClientSession(connector=connector)

        try:
            # Start schedulers for each endpoint
            for endpoint in self.config.endpoints:
                task = asyncio.create_task(self._endpoint_scheduler(endpoint))
                self._tasks.append(task)

            logger.info(f"Started {len(self._tasks)} endpoint schedulers")

            # Wait for all schedulers to complete (they run until stopped)
            await asyncio.gather(*self._tasks, return_exceptions=True)

        finally:
            # Cleanup
            if self._session:
                await self._session.close()
                self._session = None

            self._tasks.clear()
            self.state.running = False
            self.state.paused = False
            self._initialized = False
            logger.info("Load test stopped")

    async def start(self):
        """Start the load test (blocks until stopped)."""
        if self.state.running:
            logger.warning("Load test already running")
            return
        await self._run()

    def stop(self):
        """Signal the load test to stop."""
        if self._stop_event:
            logger.info("Signaling load test to stop")
            self._stop_event.set()
            if self._pause_event:
                self._pause_event.set()  # Unblock paused schedulers

    def pause(self):
        """Pause the load test."""
        if self.state.running and not self.state.paused and self._pause_event:
            logger.info("Pausing load test")
            self._pause_event.clear()
            self.state.paused = True

    def resume(self):
        """Resume the load test."""
        if self.state.running and self.state.paused and self._pause_event:
            logger.info("Resuming load test")
            self._pause_event.set()
            self.state.paused = False

    def update_endpoint_rate(self, endpoint_name: str, rate: float):
        """Update the request rate for an endpoint."""
        for endpoint in self.config.endpoints:
            if endpoint.name == endpoint_name:
                endpoint.requests_per_second = rate
                logger.info(f"Updated {endpoint_name} rate to {rate} req/s")
                break

    def enable_endpoint(self, endpoint_name: str, enabled: bool):
        """Enable or disable an endpoint."""
        for endpoint in self.config.endpoints:
            if endpoint.name == endpoint_name:
                endpoint.enabled = enabled
                logger.info(f"{'Enabled' if enabled else 'Disabled'} endpoint {endpoint_name}")
                break

    def update_streaming_ratio(self, ratio: float):
        """Update the streaming ratio."""
        self.config.streaming_ratio = max(0.0, min(1.0, ratio))
        logger.info(f"Updated streaming ratio to {self.config.streaming_ratio}")

    def get_status(self) -> dict:
        """Get current status."""
        elapsed = 0
        if self.state.start_time:
            elapsed = time.time() - self.state.start_time

        return {
            "running": self.state.running,
            "paused": self.state.paused,
            "elapsed_seconds": elapsed,
            "total_requests": self.state.total_requests_sent,
            "total_errors": self.state.total_errors,
            "endpoints": [
                {
                    "name": e.name,
                    "url": e.url,
                    "requests_per_second": e.requests_per_second,
                    "enabled": e.enabled,
                }
                for e in self.config.endpoints
            ],
            "streaming_ratio": self.config.streaming_ratio,
        }

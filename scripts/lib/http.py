"""Polite async HTTP client with per-host RPS limiting and retry."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx


class HostLimiter:
    """Per-host token bucket — at most `rps` requests per second per hostname."""

    def __init__(self, rps: float = 1.0) -> None:
        self.rps = max(rps, 0.01)
        self._next_at: dict[str, float] = defaultdict(float)
        self._lock = asyncio.Lock()

    async def wait(self, host: str) -> None:
        async with self._lock:
            now = time.monotonic()
            ready_at = self._next_at[host]
            if ready_at <= now:
                self._next_at[host] = now + 1.0 / self.rps
                return
            sleep_for = ready_at - now
            self._next_at[host] = ready_at + 1.0 / self.rps
        await asyncio.sleep(sleep_for)


class PoliteClient:
    """httpx.AsyncClient wrapper with per-host RPS, global concurrency, retry."""

    def __init__(
        self,
        user_agent: str,
        rps_per_host: float = 1.0,
        timeout: float = 15.0,
        max_concurrent: int = 500,
        max_retries: int = 3,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self._sem = asyncio.Semaphore(max_concurrent)
        self._limiter = HostLimiter(rps_per_host)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "PoliteClient":
        self._client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(self.timeout, connect=min(self.timeout, 10.0)),
            follow_redirects=True,
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            verify=True,
        )
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("PoliteClient must be used as an async context manager")
        return self._client

    async def get(self, url: str, **kwargs: Any) -> httpx.Response | None:
        """GET with per-host pacing, retry on 429/5xx, returns None on terminal failure."""
        host = urlparse(url).hostname or ""
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                async with self._sem:
                    await self._limiter.wait(host)
                    resp = await self.client.get(url, **kwargs)
                if resp.status_code in (429, 502, 503, 504):
                    backoff = min(2.0**attempt, 8.0)
                    await asyncio.sleep(backoff)
                    continue
                return resp
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout,
                    httpx.RemoteProtocolError, httpx.ReadError) as exc:
                last_exc = exc
                await asyncio.sleep(min(2.0**attempt, 8.0))
            except httpx.HTTPError as exc:
                last_exc = exc
                break
        if last_exc is not None:
            return None
        return None


@asynccontextmanager
async def polite_client(
    user_agent: str,
    rps_per_host: float = 1.0,
    timeout: float = 15.0,
    max_concurrent: int = 500,
) -> AsyncIterator[PoliteClient]:
    async with PoliteClient(
        user_agent=user_agent,
        rps_per_host=rps_per_host,
        timeout=timeout,
        max_concurrent=max_concurrent,
    ) as c:
        yield c

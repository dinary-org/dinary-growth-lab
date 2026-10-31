"""HTTP client with retries, exponential backoff, rate limiting and request logging.

Every external call goes through `Client.request` so that retries, quota
accounting and failure recording are uniform across integrations.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger("dgl.http")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"HTTP {status} for {url}: {body[:300]}")
        self.status = status
        self.url = url
        self.body = body


class NetworkBlocked(RuntimeError):
    """The host could not be reached at all (DNS/proxy policy), as opposed to an HTTP error."""


@dataclass
class RateLimiter:
    min_interval_s: float = 0.2
    _last: float = 0.0
    _lock: threading.Lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._last + self.min_interval_s - now
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


class Client:
    def __init__(self, name: str, min_interval_s: float = 0.2, max_retries: int = 4, timeout: float = 30):
        self.name = name
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "dinary-growth-lab/0.1 (+research)"
        self.limiter = RateLimiter(min_interval_s)
        self.max_retries = max_retries
        self.timeout = timeout
        self.calls = 0

    def request(self, method: str, url: str, **kw: Any) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        attempt = 0
        while True:
            self.limiter.wait()
            self.calls += 1
            try:
                resp = self.session.request(method, url, **kw)
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt >= self.max_retries:
                    raise NetworkBlocked(f"{self.name}: cannot reach {url.split('?')[0]}: {e}") from e
                self._backoff(attempt, str(e))
                attempt += 1
                continue
            if resp.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                retry_after = resp.headers.get("Retry-After")
                self._backoff(attempt, f"status {resp.status_code}", float(retry_after) if retry_after else None)
                attempt += 1
                continue
            if resp.status_code >= 400:
                raise HttpError(resp.status_code, url.split("?")[0], resp.text)
            return resp

    def get_json(self, url: str, **kw: Any) -> Any:
        return self.request("GET", url, **kw).json()

    def post_json(self, url: str, **kw: Any) -> Any:
        return self.request("POST", url, **kw).json()

    def _backoff(self, attempt: int, why: str, explicit: float | None = None) -> None:
        delay = explicit if explicit is not None else min(2 ** (attempt + 1), 30) + random.random()
        log.warning("%s retry %d in %.1fs (%s)", self.name, attempt + 1, delay, why)
        time.sleep(delay)

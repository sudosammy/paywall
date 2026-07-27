"""HTTP helpers: retry on 429 using Retry-After (or short backoff)."""

from __future__ import annotations

import logging
import time
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_MAX_WAIT = 30.0
DEFAULT_BACKOFF = (1.0, 2.0, 4.0)


def retry_after_seconds(
    response: Any,
    attempt: int,
    *,
    max_wait: float = DEFAULT_MAX_WAIT,
    backoff: tuple[float, ...] = DEFAULT_BACKOFF,
) -> float:
    """Seconds to wait before retrying a 429 response."""
    header = response.headers.get("Retry-After")
    wait: float | None = None
    if header:
        try:
            wait = float(header)
        except ValueError:
            try:
                when = parsedate_to_datetime(header)
                wait = max(0.0, when.timestamp() - time.time())
            except (TypeError, ValueError, IndexError, OverflowError):
                wait = None
    if wait is None:
        wait = backoff[min(attempt, len(backoff) - 1)]
    return min(max(wait, 0.0), max_wait)


def _impersonated_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 15.0,
    impersonate: str = "chrome",
) -> Any:
    """GET via curl_cffi with browser TLS fingerprint impersonation."""
    from curl_cffi import requests as creq

    return creq.get(
        url,
        headers=headers,
        params=params,
        timeout=timeout,
        impersonate=impersonate,
    )


def get_with_retries(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 15.0,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    sleep: Callable[[float], None] | None = None,
    impersonate: str | None = None,
) -> Any:
    """GET *url*, retrying on HTTP 429 with Retry-After / backoff.

    When *impersonate* is set (e.g. ``"chrome"``), the request uses curl_cffi
    with browser TLS fingerprinting so Yahoo and similar anti-bot endpoints
    accept the call. Otherwise httpx is used (Frankfurter, etc.).
    """
    sleeper = sleep or time.sleep
    last_response: Any = None

    if impersonate is not None:
        for attempt in range(max_attempts):
            resp = _impersonated_get(
                url,
                headers=headers,
                params=params,
                timeout=timeout,
                impersonate=impersonate,
            )
            if resp.status_code != 429:
                return resp
            last_response = resp
            if attempt >= max_attempts - 1:
                break
            wait = retry_after_seconds(resp, attempt)
            logger.warning(
                "HTTP 429 from %s; retrying in %.1fs (attempt %s/%s)",
                url,
                wait,
                attempt + 1,
                max_attempts,
            )
            sleeper(wait)
        assert last_response is not None
        return last_response

    with httpx.Client(timeout=timeout) as client:
        for attempt in range(max_attempts):
            resp = client.get(url, headers=headers, params=params)
            if resp.status_code != 429:
                return resp
            last_response = resp
            if attempt >= max_attempts - 1:
                break
            wait = retry_after_seconds(resp, attempt)
            logger.warning(
                "HTTP 429 from %s; retrying in %.1fs (attempt %s/%s)",
                url,
                wait,
                attempt + 1,
                max_attempts,
            )
            sleeper(wait)
    assert last_response is not None
    return last_response

"""Observability utility for rate-limit backoff delays.

Provides ``async_backoff`` — a drop-in replacement for ``asyncio.sleep``
that sleeps the wait time determined by the caller and logs a WARNING
only when that wait exceeds a configurable threshold.  The wait time
typically comes from the provider: the ``Retry-After`` header on an HTTP
429 response, an exponential backoff schedule, or the rate limiter's
token-bucket refill estimate.  The caller passes that value in, so the
helper makes an otherwise-silent sleep visible in structured logs and
records which service was throttled and for how long.

Usage::

    from langshark_bites.api_backoff import async_backoff, retry_after_seconds

    # Provider says to wait before retrying (Retry-After header);
    # fall back to 30s if the header is missing:
    wait = retry_after_seconds(response) or 30.0
    await async_backoff(wait, context="NewsAPI retry 1/3")

The warning threshold is passed as a parameter (default 10s) so callers
can adapt it to their own project's needs.
"""

from __future__ import annotations

import asyncio

import structlog

log = structlog.get_logger(__name__)


async def async_backoff(
    delay_sec: float,
    *,
    context: str = "",
    warning_threshold_sec: float = 10.0,
) -> None:
    """Sleep *delay_sec* seconds, logging WARNING if the delay exceeds
    ``warning_threshold_sec``.

    Args:
        delay_sec: The number of seconds to sleep (non-blocking async sleep).
        context: A short description of what is being rate-limited (e.g.
            ``"NewsAPI retry 2/3"``, ``"LLM:deepseek-chat retry 1/3"``).
        warning_threshold_sec: Log a WARNING when *delay_sec* is >= this
            threshold.  Set to 0 to log every retry; set to a large value
            to suppress completely.
    """
    if delay_sec >= warning_threshold_sec:
        log.warning(
            "rate_limit_backoff",
            delay_sec=round(delay_sec, 1),
            context=context,
        )

    await asyncio.sleep(delay_sec)


def retry_after_seconds(response) -> float | None:
    """Return the wait time (seconds) from an HTTP response's ``Retry-After``.

    Accepts any object with a ``.headers`` mapping (for example
    ``httpx.Response``, ``requests.Response``, or an ASGI response).  Handles
    both forms allowed by the HTTP spec (RFC 9110):

    - an integer number of seconds: ``Retry-After: 30`` -> ``30.0``
    - an HTTP-date: ``Retry-After: Wed, 21 Oct 2015 07:28:00 GMT`` ->
      seconds from now until that instant.

    Returns ``None`` if the header is absent or cannot be parsed, so callers
    can fall back to a fixed backoff.

    Usage::

        from langshark_bites.api_backoff import async_backoff, retry_after_seconds

        wait = retry_after_seconds(response) or 30.0
        await async_backoff(wait, context="NewsAPI retry 1/3")
    """
    from email.utils import parsedate_to_datetime

    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("Retry-After")
    if value is None:
        return None
    value = str(value).strip()

    # 1) Integer seconds ("Retry-After: 30")
    try:
        return float(value)
    except ValueError:
        pass

    # 2) HTTP-date ("Retry-After: Wed, 21 Oct 2015 07:28:00 GMT")
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            import datetime as _dt

            retry_at = retry_at.replace(tzinfo=_dt.timezone.utc)
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        return max(0.0, (retry_at - now).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None

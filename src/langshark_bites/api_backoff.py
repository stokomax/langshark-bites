"""Observability utility for rate-limit backoff delays.

Provides ``async_backoff`` — a drop-in replacement for ``asyncio.sleep``
that logs a WARNING when the delay exceeds a configurable threshold.
Used anywhere rate limiting triggers a sleep so operators can see
LLM provider / NewsAPI / any future rate-limited service backoffs in
structured logs.

Usage::

    from langshark_bites.api_backoff import async_backoff
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

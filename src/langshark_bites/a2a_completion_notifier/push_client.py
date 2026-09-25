"""HTTP client that POSTs A2A push notifications with retry/backoff.

Why this exists
---------------
The emitter middleware produces a signed notification and must deliver it
at-least-once.  The A2A spec recommends 10-30s webhook timeouts with retry
and backoff, so the POST path needs its own retry policy independent of the
surrounding SDK client.

This module is deliberately thin: it takes an already-signed Bearer token
and a JSON-serializable payload and POSTs them to the registered webhook,
returning a bool so middleware can log-and-continue rather than raise into
the agent loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PushClientSettings:
    """Retry policy for A2A webhook delivery.

    Attributes:
        timeout_seconds: Per-attempt HTTP timeout.
        max_retries: Extra attempts after the first POST.
        retry_backoff_seconds: Base of the exponential backoff
            (``base * 2**attempt`` sleeps between attempts).
    """

    timeout_seconds: float = 30.0
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0


class PushClient:
    """POSTs signed A2A notifications to a webhook URL with backoff."""

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        settings: PushClientSettings | None = None,
    ) -> None:
        """POST notifications over ``http``, retrying per ``settings``.

        Args:
            http: Optional client to reuse.  When omitted, one is created and
                owned by this instance (closed by :meth:`aclose`).
            settings: Retry/backoff settings.  Defaults to ``PushClientSettings()``.
        """
        self._http = http or httpx.AsyncClient()
        self._owned_http = http is None
        self._settings = settings if settings is not None else PushClientSettings()

    async def send(
        self,
        url: str,
        *,
        bearer: str | None = None,
        payload: dict[str, Any],
    ) -> bool:
        """Deliver one notification; retry on failure with backoff.

        Args:
            url: The webhook URL from ``PushNotificationConfig['url']``.
            bearer: The signed sender JWT (goes in the Authorization header).
                ``None`` sends the notification unsigned -- the ``dev`` /
                ``verify``-unsigned receiver path.
            payload: JSON-serializable notification body.

        Returns:
            True if any attempt got an HTTP 2xx; False if all attempts
            failed.  Never raises -- callers log-and-continue.
        """
        headers = {}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        last_error: Exception | None = None
        total_attempts = self._settings.max_retries + 1

        for attempt in range(total_attempts):
            try:
                response = await self._http.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self._settings.timeout_seconds,
                )
                if response.is_success:
                    return True
                last_error = RuntimeError(f"webhook returned HTTP {response.status_code}")
            except httpx.HTTPError as exc:
                last_error = exc

            if attempt < self._settings.max_retries:
                sleep_for = self._settings.retry_backoff_seconds * (2**attempt)
                log.warning(
                    "a2a_push_retry",
                    url=url,
                    attempt=attempt + 1,
                    retry_in=sleep_for,
                    error=str(last_error),
                )
                await asyncio.sleep(sleep_for)

        log.error(
            "a2a_push_failed",
            url=url,
            attempts=total_attempts,
            error=str(last_error),
        )
        return False

    async def aclose(self) -> None:
        """Close the HTTP client if this instance created it."""
        if self._owned_http:
            await self._http.aclose()

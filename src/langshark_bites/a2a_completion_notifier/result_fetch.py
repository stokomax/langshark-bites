r"""Fetch the full result of a completed subagent task (fetch-only primitive).

Why this exists
---------------
The completion notifier already proves a subagent run reached a terminal
state -- the webhook arrived, was authenticated and deduplicated, and the
mailbox delivered it.  The supervisor therefore never needs to *poll* for
completion.  The async-agent framework's ``check_async_task`` couples two
concerns (status + result) and assumes the caller knows neither; this module
is the **sister primitive** that assumes the caller knows the first and only
gets the second:

- ``check_async_task(task_id)``   -- status round-trip, then maybe a fetch.
- ``fetch_task_result(task_id)``  -- a single on-demand fetch, no status gate.

The full response is read from the subagent deployment's Agent Protocol
thread state (``GET /threads/{task_id}/state``).  That works because the
emitter resolves the notification ``task_id`` to the subagent's ``thread_id``
(see ``middleware._default_task_id``), not its per-run ``run_id`` -- the same
\"thread ID == task ID\" convention the async-agent framework uses.

Because the receiver pins a single ``subagent_issuer`` / ``subagent_jwks_url``
today, the deployment is also configured, not discovered: ``subagent_url`` is
the Agent Server base URL whose ``/threads/`` namespace holds the task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


class TaskResultError(Exception):
    """The full-result fetch failed (unconfigured, unreachable, or non-2xx)."""


@dataclass(frozen=True)
class ResultFetchSettings:
    """Configuration for fetching a completed subagent's full result.

    Attributes:
        subagent_url: Agent Server base URL of the subagent deployment.  The
            thread-state endpoint is ``<subagent_url>/threads/<task_id>/state``.
        subagent_api_key: Optional bearer token for that deployment.
        timeout_seconds: Per-attempt HTTP timeout.
    """

    subagent_url: str = ""
    subagent_api_key: str = ""
    timeout_seconds: float = 15.0


async def fetch_task_result(
    task_id: str,
    *,
    settings: ResultFetchSettings,
    http: httpx.AsyncClient,
) -> dict[str, Any]:
    """Fetch the full thread state for a completed subagent task.

    One round trip, no status check: the completion notifier established that
    the task is terminal, so the caller is expected to already know it is done.

    Args:
        task_id: The notification's ``task_id`` (the subagent's thread id).
        settings: Fetch configuration (subagent URL + optional API key).
        http: HTTP client used for the outbound request.

    Returns:
        The subagent's thread state (Agent Protocol ``GET /threads/{id}/state``
        JSON body).

    Raises:
        TaskResultError: If no ``subagent_url`` is configured, the request
            fails, or the subagent returns a non-2xx response.
    """
    if not settings.subagent_url:
        raise TaskResultError("no A2A_SUBAGENT_URL configured; cannot fetch the task result")
    url = f"{settings.subagent_url.rstrip('/')}/threads/{quote(str(task_id), safe='')}/state"
    headers: dict[str, str] = {}
    if settings.subagent_api_key:
        headers["Authorization"] = f"Bearer {settings.subagent_api_key}"
    try:
        response = await http.get(url, headers=headers, timeout=settings.timeout_seconds)
    except httpx.HTTPError as exc:
        raise TaskResultError(f"result fetch failed for task {task_id!r}: {exc}") from exc
    if not response.is_success:
        raise TaskResultError(f"subagent returned HTTP {response.status_code} for task {task_id!r}")
    try:
        data = response.json()
    except ValueError as exc:
        raise TaskResultError(
            f"subagent returned a malformed (non-JSON) body for task {task_id!r}"
        ) from exc
    if not isinstance(data, dict):
        raise TaskResultError(
            f"subagent returned non-object JSON for task {task_id!r}: {type(data).__name__}"
        )
    return data

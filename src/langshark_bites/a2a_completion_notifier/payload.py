"""Wire format for A2A completion notifications (emitter -> receiver).

Why this exists
---------------
Both ends of the notifier must agree on the notification body.  The
emitter (subagent side) *builds* it; the receiver (supervisor side) *parses*
it.  This module is the single home for that contract so either side can
change without drifting.

Completion profile (A2A Task, sparse)
-------------------------------------
Webhook bodies are **A2A ``Task`` objects** (official ``a2a-sdk`` / proto JSON),
populated only as far as a terminal completion needs::

    {
        "id": "<task id>",
        "contextId": "<optional A2A context / supervisor thread>",
        "status": {"state": "TASK_STATE_COMPLETED"},
        "metadata": {
            "token": "<opaque callback token>",
            "summary": "...optional extension..."
        }
    }

``metadata.token`` remains the supervisor-minted opaque callback token
(extension for routing).  Core identity is the A2A Task fields
(``id`` / ``contextId`` / ``status.state``).

Legacy bite dicts (``status.state`` as ``completed`` and top-level ``summary``)
are still accepted by the extract helpers for one compatibility window.
"""

from __future__ import annotations

from typing import Any

from a2a.types import Task, TaskState
from google.protobuf.json_format import MessageToDict, ParseDict

# App-facing terminal labels (mailbox / drain / logs).
TERMINAL_STATES = frozenset({"completed", "failed", "canceled", "rejected"})

# A2A TaskState JSON enum names (proto3 JSON).
_APP_TO_A2A_STATE: dict[str, str] = {
    "completed": "TASK_STATE_COMPLETED",
    "failed": "TASK_STATE_FAILED",
    "canceled": "TASK_STATE_CANCELED",
    "cancelled": "TASK_STATE_CANCELED",  # common spelling
    "rejected": "TASK_STATE_REJECTED",
    "working": "TASK_STATE_WORKING",
    "submitted": "TASK_STATE_SUBMITTED",
    "input_required": "TASK_STATE_INPUT_REQUIRED",
    "auth_required": "TASK_STATE_AUTH_REQUIRED",
}

_A2A_TO_APP_STATE: dict[str, str] = {
    "TASK_STATE_COMPLETED": "completed",
    "TASK_STATE_FAILED": "failed",
    "TASK_STATE_CANCELED": "canceled",
    "TASK_STATE_REJECTED": "rejected",
    "TASK_STATE_WORKING": "working",
    "TASK_STATE_SUBMITTED": "submitted",
    "TASK_STATE_INPUT_REQUIRED": "input_required",
    "TASK_STATE_AUTH_REQUIRED": "auth_required",
    "TASK_STATE_UNSPECIFIED": "unspecified",
}


class NotificationPayloadError(ValueError):
    """Webhook body is not a valid (sparse) A2A Task notification."""


def _a2a_state_name(state: str) -> str:
    """Map app label or A2A enum name to the proto JSON enum string."""
    if state in _A2A_TO_APP_STATE:
        return state
    key = state.strip().lower().replace("-", "_")
    if key in _APP_TO_A2A_STATE:
        return _APP_TO_A2A_STATE[key]
    raise NotificationPayloadError(f"unsupported task state {state!r}")


def _app_state_name(state: str | None) -> str | None:
    """Map A2A enum or legacy short label to app terminal/non-terminal label."""
    if state is None:
        return None
    if state in TERMINAL_STATES or state in _APP_TO_A2A_STATE:
        return "canceled" if state == "cancelled" else state
    return _A2A_TO_APP_STATE.get(state)


def build_notification(
    task_id: str,
    state: str,
    token: str,
    *,
    context_id: str | None = None,
    summary: str | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a sparse A2A ``Task`` body for the receiver webhook.

    Args:
        task_id: A2A task id (``Task.id``).
        state: App label (``completed`` / ``failed`` / ...) or A2A enum name.
        token: Opaque callback token from ``PushNotificationConfig`` (extension).
        context_id: Optional A2A ``contextId`` (e.g. supervisor thread id).
        summary: Optional short summary stored under ``metadata.summary``.
        artifacts: Optional A2A artifact dicts (advanced; usually omitted).

    Returns:
        JSON-serializable A2A Task dict (proto JSON / camelCase).

    Raises:
        NotificationPayloadError: Unknown state or invalid Task after build.
    """
    a2a_state = _a2a_state_name(state)
    meta: dict[str, Any] = {"token": token}
    if summary is not None:
        meta["summary"] = summary

    body: dict[str, Any] = {
        "id": task_id,
        "status": {"state": a2a_state},
        "metadata": meta,
    }
    if context_id:
        body["contextId"] = context_id
    if artifacts is not None:
        body["artifacts"] = artifacts

    # Validate round-trip through the official Task type.
    return task_to_dict(parse_a2a_task(body))


def parse_a2a_task(payload: dict[str, Any]) -> Task:
    """Parse and validate ``payload`` as an A2A ``Task``.

    Raises:
        NotificationPayloadError: Not a valid Task message.
    """
    if not isinstance(payload, dict):
        raise NotificationPayloadError("notification payload must be a JSON object")
    try:
        task = ParseDict(payload, Task(), ignore_unknown_fields=True)
    except Exception as exc:
        raise NotificationPayloadError(f"invalid A2A Task: {exc}") from exc
    if not task.id:
        raise NotificationPayloadError("A2A Task.id is required")
    if not task.HasField("status") or task.status.state == TaskState.TASK_STATE_UNSPECIFIED:
        raise NotificationPayloadError("A2A Task.status.state is required")
    return task


def task_to_dict(task: Task) -> dict[str, Any]:
    """Serialize an A2A ``Task`` to proto JSON (camelCase)."""
    return MessageToDict(task, preserving_proto_field_name=False)


def normalize_notification_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return an A2A Task dict, accepting legacy bite bodies for one release.

    Legacy shape::

        {"id", "status": {"state": "completed"}, "metadata": {"token"}, "summary"?}

    Raises:
        NotificationPayloadError: Cannot interpret as Task or legacy completion.
    """
    if not isinstance(payload, dict):
        raise NotificationPayloadError("notification payload must be a JSON object")

    # Fast path: already A2A (enum state names).
    raw_state = (payload.get("status") or {}).get("state")
    if isinstance(raw_state, str) and raw_state.startswith("TASK_STATE_"):
        return task_to_dict(parse_a2a_task(payload))

    # Legacy / app-label state → upgrade via build_notification fields.
    task_id = payload.get("id") or payload.get("taskId")
    if not task_id:
        raise NotificationPayloadError("notification payload missing Task.id")
    if not isinstance(raw_state, str):
        raise NotificationPayloadError("notification payload missing status.state")

    token = None
    meta = payload.get("metadata")
    if isinstance(meta, dict):
        token = meta.get("token")
    if not token:
        # Cannot build a full Task without the routing extension; try strict parse
        # in case metadata uses another shape.
        try:
            return task_to_dict(parse_a2a_task(payload))
        except NotificationPayloadError:
            raise NotificationPayloadError(
                "notification payload is missing metadata.token"
            ) from None

    summary = None
    if isinstance(meta, dict) and meta.get("summary") is not None:
        summary = str(meta.get("summary"))
    elif payload.get("summary") is not None:
        summary = str(payload.get("summary"))

    context_id = payload.get("contextId") or payload.get("context_id")
    return build_notification(
        str(task_id),
        raw_state,
        str(token),
        context_id=str(context_id) if context_id else None,
        summary=summary,
        artifacts=payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else None,
    )


def extract_task_id(payload: dict[str, Any]) -> str:
    """Return ``Task.id`` from a (normalized) notification body."""
    task_id = payload.get("id") or payload.get("taskId")
    if not task_id:
        raise NotificationPayloadError("notification payload missing Task.id")
    return str(task_id)


def extract_context_id(payload: dict[str, Any]) -> str | None:
    """Return ``Task.contextId`` when present."""
    ctx = payload.get("contextId") or payload.get("context_id")
    return str(ctx) if ctx else None


def extract_terminal_state(payload: dict[str, Any]) -> str | None:
    """Return the app terminal state label, or None if non-terminal/unknown.

    Accepts A2A enum names and legacy short labels.
    """
    state = (payload.get("status") or {}).get("state")
    app = _app_state_name(str(state) if state is not None else None)
    if app in TERMINAL_STATES:
        return app
    return None


def extract_callback_token(payload: dict[str, Any]) -> str:
    """Return the opaque callback token from ``metadata.token``."""
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("notification payload metadata must be an object")
    token = metadata.get("token")
    if not token:
        raise ValueError("notification payload is missing metadata.token")
    return str(token)


def extract_summary(payload: dict[str, Any]) -> str | None:
    """Return optional summary from ``metadata.summary`` or legacy top-level."""
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and metadata.get("summary") is not None:
        return str(metadata["summary"])
    if payload.get("summary") is not None:
        return str(payload["summary"])
    return None

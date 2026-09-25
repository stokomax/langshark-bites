"""Unit tests for A2A Task-shaped completion payloads."""

from __future__ import annotations

import pytest

from langshark_bites.a2a_completion_notifier.payload import (
    NotificationPayloadError,
    build_notification,
    extract_callback_token,
    extract_context_id,
    extract_summary,
    extract_task_id,
    extract_terminal_state,
    normalize_notification_payload,
    parse_a2a_task,
)


class TestBuildNotification:
    def test_emits_sparse_a2a_task(self) -> None:
        body = build_notification(
            "task-1",
            "completed",
            "tok",
            context_id="ctx-1",
            summary="done",
        )
        assert body["id"] == "task-1"
        assert body["contextId"] == "ctx-1"
        assert body["status"]["state"] == "TASK_STATE_COMPLETED"
        assert body["metadata"]["token"] == "tok"
        assert body["metadata"]["summary"] == "done"
        # Round-trip through official Task type.
        parse_a2a_task(body)

    def test_rejects_unknown_state(self) -> None:
        with pytest.raises(NotificationPayloadError):
            build_notification("t", "not-a-state", "tok")


class TestNormalize:
    def test_upgrades_legacy_short_state(self) -> None:
        legacy = {
            "id": "t1",
            "status": {"state": "completed"},
            "metadata": {"token": "tok"},
            "summary": "hi",
        }
        body = normalize_notification_payload(legacy)
        assert body["status"]["state"] == "TASK_STATE_COMPLETED"
        assert extract_summary(body) == "hi"
        assert extract_terminal_state(body) == "completed"

    def test_invalid_object_rejected(self) -> None:
        with pytest.raises(NotificationPayloadError):
            normalize_notification_payload({"foo": 1})  # type: ignore[arg-type]


class TestExtractors:
    def test_extractors_on_a2a_body(self) -> None:
        body = build_notification("tid", "failed", "cb", context_id="c1", summary="x")
        assert extract_task_id(body) == "tid"
        assert extract_context_id(body) == "c1"
        assert extract_terminal_state(body) == "failed"
        assert extract_callback_token(body) == "cb"
        assert extract_summary(body) == "x"

    def test_non_terminal_returns_none(self) -> None:
        body = build_notification("tid", "working", "cb")
        assert extract_terminal_state(body) is None

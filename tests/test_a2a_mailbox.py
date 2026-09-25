"""Tests for langshark_bites.a2a_completion_notifier.mailbox."""

from __future__ import annotations

import pytest

from langshark_bites.a2a_completion_notifier.mailbox import (
    DEFAULT_WAKE_INPUT,
    MailboxWakeError,
    MailboxWriteError,
    NotificationMailbox,
)
from langshark_bites.a2a_completion_notifier.tokens import CallbackToken

_parent = CallbackToken(thread_id="thread_1", assistant_id="asst_1", dispatch_id="d1", exp=0)


class _FakeStore:
    def __init__(self) -> None:
        self.items: list[dict] = []

    async def put_item(self, namespace, key, value) -> None:
        self.items.append({"namespace": namespace, "key": key, "value": value})


class _FakeRuns:
    def __init__(self, active: bool = False, fail_list: bool = False) -> None:
        self.active = active
        self.fail_list = fail_list
        self.created: list[dict] = []

    async def list(self, thread_id, *, status, limit):
        if self.fail_list:
            raise RuntimeError("runs.list failed")
        return ["run-pending"] if self.active else []

    async def create(self, **kwargs) -> None:
        self.created.append(kwargs)


class _FakeLG:
    def __init__(self, store: _FakeStore, runs: _FakeRuns) -> None:
        self.store = store
        self.runs = runs


def _mailbox(lg) -> NotificationMailbox:
    return NotificationMailbox(lg)


class TestDeliver:
    async def test_writes_to_store_namespaced_by_thread(self):
        store = _FakeStore()
        lg = _FakeLG(store, _FakeRuns())
        mailbox = _mailbox(lg)

        await mailbox.deliver(_parent, "task-1", "completed", summary="done")

        assert len(store.items) == 1
        item = store.items[0]
        assert item["namespace"] == ("notifications", "thread_1")
        assert item["key"] == "task-1"
        assert item["value"]["state"] == "completed"
        assert item["value"]["dispatch_id"] == "d1"
        assert item["value"]["summary"] == "done"

    async def test_store_failure_raises_mailbox_write_error(self):
        class BrokenStore:
            async def put_item(self, namespace, key, value):
                raise RuntimeError("store is down")

        lg = _FakeLG(BrokenStore(), _FakeRuns())
        mailbox = _mailbox(lg)
        with pytest.raises(MailboxWriteError):
            await mailbox.deliver(_parent, "task-1", "completed")


class TestTriggerIfIdle:
    async def test_creates_wake_run_when_idle(self):
        runs = _FakeRuns(active=False)
        lg = _FakeLG(_FakeStore(), runs)
        mailbox = _mailbox(lg)

        result = await mailbox.trigger_if_idle(_parent)
        assert result is True
        assert len(runs.created) == 1
        create = runs.created[0]
        assert create["thread_id"] == "thread_1"
        assert create["assistant_id"] == "asst_1"
        assert create["multitask_strategy"] == "enqueue"
        # The wake run must always carry real input: a run with ``None``
        # input is a silent no-op on an idle thread (no node executes, so the
        # before_model drain never fires).  Default is DEFAULT_WAKE_INPUT.
        assert create.get("input") is not None
        assert create["input"] == DEFAULT_WAKE_INPUT

    async def test_does_not_create_when_active(self):
        runs = _FakeRuns(active=True)
        lg = _FakeLG(_FakeStore(), runs)
        mailbox = _mailbox(lg)

        result = await mailbox.trigger_if_idle(_parent)
        assert result is False
        assert runs.created == []

    async def test_list_failure_returns_false_and_does_not_create(self):
        runs = _FakeRuns(active=False, fail_list=True)
        lg = _FakeLG(_FakeStore(), runs)
        mailbox = _mailbox(lg)

        result = await mailbox.trigger_if_idle(_parent)
        assert result is False
        assert runs.created == []

    async def test_create_failure_raises_mailbox_wake_error(self):
        class FailingRuns(_FakeRuns):
            async def create(self, **kwargs):
                raise RuntimeError("create failed")

        runs = FailingRuns(active=False)
        lg = _FakeLG(_FakeStore(), runs)
        with pytest.raises(MailboxWakeError):
            await NotificationMailbox(lg).trigger_if_idle(_parent)

    async def test_wake_input_passed_to_create(self):
        runs = _FakeRuns(active=False)
        lg = _FakeLG(_FakeStore(), runs)
        mailbox = NotificationMailbox(lg, wake_input={"question": "what changed?"})
        assert await mailbox.trigger_if_idle(_parent) is True
        assert runs.created[0]["input"] == {"question": "what changed?"}

    async def test_wake_input_none_omits_input(self):
        # Explicit opt-out for interrupt/resume flows: the run is created
        # without an ``input`` key at all (original behavior).
        runs = _FakeRuns(active=False)
        lg = _FakeLG(_FakeStore(), runs)
        mailbox = NotificationMailbox(lg, wake_input=None)
        assert await mailbox.trigger_if_idle(_parent) is True
        assert "input" not in runs.created[0]

"""Tests for langshark_bites.a2a_completion_notifier.drain."""

from __future__ import annotations

import pytest
from langgraph.store.memory import InMemoryStore

from langshark_bites.a2a_completion_notifier.drain import (
    MailboxDrainError,
    MailboxDrainMiddleware,
    render_notice,
)


def _fake_runtime(store, thread_id="thread_1"):
    info = type("ExecInfo", (), {"thread_id": thread_id})()
    return type("Runtime", (), {"store": store, "execution_info": info})()


def _mailbox_with(store: InMemoryStore, thread_id="thread_1", notices=None):
    """Seed the mailbox namespace with completion notices."""
    notices = notices or [
        {"task_id": "task-1", "state": "completed", "summary": "alpha"},
        {"task_id": "task-2", "state": "failed", "summary": "beta"},
    ]
    for notice in notices:
        store.put(
            ("notifications", thread_id),
            notice["task_id"],
            {k: v for k, v in notice.items() if k != "task_id"},
        )
    return notices


class TestRenderNotice:
    def test_renders_completion_block(self):
        text = render_notice("task-9", {"state": "completed", "summary": "ok"})
        assert "[SUBAGENT COMPLETION NOTICE]" in text
        assert "task_id: task-9" in text
        assert "status: completed" in text
        assert "summary: ok" in text


class TestDrainMiddleware:
    async def test_drains_pending_notices_into_message(self):
        store = InMemoryStore()
        _mailbox_with(store)
        mw = MailboxDrainMiddleware()

        update = await mw.abefore_model({}, _fake_runtime(store))

        assert update is not None
        messages = update["messages"]
        assert len(messages) == 1
        content = messages[0].content
        assert "task-1" in content
        assert "task-2" in content
        assert "alpha" in content
        assert "beta" in content

    async def test_consumes_mailbox_after_drain(self):
        store = InMemoryStore()
        _mailbox_with(store)
        mw = MailboxDrainMiddleware()

        await mw.abefore_model({}, _fake_runtime(store))
        # A second drain must find the mailbox empty.
        second = await mw.abefore_model({}, _fake_runtime(store))
        assert second is None

    async def test_no_store_and_unreachable_fallback_raises(self, monkeypatch):
        """No runtime.store + an unreachable HTTP fallback surfaces as an error."""

        def _raise_connect_error() -> None:
            raise OSError("A2A_SUPERVISOR_URL unreachable")

        monkeypatch.setattr(
            "langshark_bites.a2a_completion_notifier.drain._http_store_from_env",
            _raise_connect_error,
        )
        mw = MailboxDrainMiddleware()
        with pytest.raises(MailboxDrainError):
            await mw.abefore_model({}, _fake_runtime(None))

    async def test_empty_mailbox_is_noop(self):
        store = InMemoryStore()
        mw = MailboxDrainMiddleware()
        update = await mw.abefore_model({}, _fake_runtime(store))
        assert update is None

    async def test_no_thread_id_is_noop(self):
        store = InMemoryStore()
        mw = MailboxDrainMiddleware()
        runtime = type("Runtime", (), {"store": store, "execution_info": None})()
        update = await mw.abefore_model({}, runtime)
        assert update is None

    async def test_search_failure_raises_mailbox_drain_error(self):
        class BrokenStore:
            async def asearch(self, namespace, limit=None):
                raise RuntimeError("store down")

            async def adelete(self, namespace, key):
                pass

        mw = MailboxDrainMiddleware()
        with pytest.raises(MailboxDrainError):
            await mw.abefore_model({}, _fake_runtime(BrokenStore()))

    async def test_only_own_thread_drained(self):
        store = InMemoryStore()
        _mailbox_with(store, thread_id="thread_1")
        _mailbox_with(
            store,
            thread_id="thread_other",
            notices=[{"task_id": "task-x", "state": "completed", "summary": "other"}],
        )
        mw = MailboxDrainMiddleware()

        update = await mw.abefore_model({}, _fake_runtime(store, thread_id="thread_1"))
        assert update is not None
        assert "task-x" not in update["messages"][0].content
        assert "task-1" in update["messages"][0].content

    async def test_custom_render_notice(self):
        store = InMemoryStore()
        _mailbox_with(store, notices=[{"task_id": "task-1", "state": "completed"}])
        mw = MailboxDrainMiddleware(render_notice=lambda task_id, value: f"CUSTOM:{task_id}")
        update = await mw.abefore_model({}, _fake_runtime(store))
        assert update is not None
        assert update["messages"][0].content == "CUSTOM:task-1"

    async def test_delete_failure_still_injects_rendered_notices(self):
        class DeleteBrokenStore(InMemoryStore):
            async def adelete(self, namespace, task_id) -> None:
                raise RuntimeError("delete failed")

        store = DeleteBrokenStore()
        _mailbox_with(store)
        mw = MailboxDrainMiddleware()
        update = await mw.abefore_model({}, _fake_runtime(store))
        # The notice is rendered and injected even though the delete failed;
        # the file already contains the task ids (may be re-drained next call).
        assert update is not None
        assert "task-1" in update["messages"][0].content
        assert store.search(("notifications", "thread_1"))  # not consumed

    async def test_max_notices_caps_a_single_drain(self):
        store = InMemoryStore()
        _mailbox_with(
            store,
            notices=[{"task_id": f"task-{i}", "state": "completed"} for i in range(5)],
        )
        mw = MailboxDrainMiddleware(max_notices=2)
        update = await mw.abefore_model({}, _fake_runtime(store))
        assert update is not None
        content = update["messages"][0].content
        assert "task-0" in content and "task-1" in content  # first two only
        assert "task-2" not in content
        assert len(store.search(("notifications", "thread_1"))) == 3  # rest remain

    async def test_http_store_fallback_builds_sdk_store(self, monkeypatch):
        """No runtime.store → the drain uses a langgraph-sdk StoreClient."""

        class FakeSDKStore:
            def __init__(self) -> None:
                self.items: list[dict] = [
                    {
                        "namespace": ["notifications", "http-thread"],
                        "key": "task-http",
                        "value": {"state": "completed", "summary": "hi"},
                    }
                ]

            async def search_items(self, namespace, *, limit=10):
                ns = list(namespace)
                matched = [i for i in self.items if i["namespace"] == ns][:limit]
                return {"items": matched}

            async def delete_item(self, namespace, key) -> None:
                self.items = [
                    i
                    for i in self.items
                    if not (i["namespace"] == list(namespace) and i["key"] == key)
                ]

        fake_store = FakeSDKStore()
        monkeypatch.setattr(
            "langshark_bites.a2a_completion_notifier.drain._http_store_from_env",
            lambda: fake_store,
        )
        mw = MailboxDrainMiddleware()
        runtime = type(
            "Runtime",
            (),
            {"store": None, "execution_info": type("E", (), {"thread_id": "http-thread"})()},
        )()
        update = await mw.abefore_model({}, runtime)

        assert update is not None
        content = update["messages"][0].content
        assert "task-http" in content
        assert "hi" in content
        assert fake_store.items == []  # consumed via delete_item

    async def test_runtime_store_still_wins_when_explicit(self):
        """An explicitly-passed store is used even when runtime.store exists."""
        explicit = InMemoryStore()
        _mailbox_with(explicit)
        other = InMemoryStore()
        _mailbox_with(
            other,
            thread_id="other",
            notices=[{"task_id": "task-x", "state": "completed"}],
        )

        mw = MailboxDrainMiddleware(store=explicit)
        update = await mw.abefore_model({}, _fake_runtime(other))
        assert update is not None
        assert "task-1" in update["messages"][0].content
        assert "task-x" not in update["messages"][0].content
        assert explicit.search(("notifications", "thread_1")) == []  # consumed

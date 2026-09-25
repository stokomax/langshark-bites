"""End-to-end test: emitter graph -> receiver -> mailbox -> supervisor drain.

Drives both halves of the notifier through real compiled ``create_agent``
graphs with ``GenericFakeChatModel``, so the LangChain runtime itself
exercises the middleware hook contract (``abefore_agent`` / ``aafter_agent`` /
``awrap_model_call``) that the unit tests only call by hand.
"""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langgraph.store.memory import InMemoryStore

from langshark_bites.a2a_completion_notifier.auth import JWKSClient
from langshark_bites.a2a_completion_notifier.drain import MailboxDrainMiddleware
from langshark_bites.a2a_completion_notifier.idempotency import InMemoryJtiStore
from langshark_bites.a2a_completion_notifier.mailbox import NotificationMailbox
from langshark_bites.a2a_completion_notifier.middleware import (
    build_a2a_notifier_from_config,
)
from langshark_bites.a2a_completion_notifier.push_client import PushClient
from langshark_bites.a2a_completion_notifier.receiver import create_receiver_app
from langshark_bites.a2a_completion_notifier.settings import ReceiverSettings

RECEIVER_URL = "https://receiver.example"
ISSUER = "https://subagents.example"
CALLBACK_TOKEN_SECRET = "super-secret-callback-token-secret"


class _RaisingChatModel(BaseChatModel):
    """Fake model whose async generation crashes, to exercise the error path."""

    @property
    def _llm_type(self) -> str:
        return "raising"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("model crash")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("model crash")


class _FakeRuns:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def list(self, thread_id, *, status, limit):
        return []

    async def create(self, **kwargs) -> None:
        self.created.append(kwargs)


class _StoreAdapter:
    """lg.store view: InMemoryStore has no async ``put_item`` (verified)."""

    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def put_item(self, namespace, key, value) -> None:
        self._store.put(namespace, key, value)


class _LG:
    def __init__(self, store: InMemoryStore) -> None:
        self.store = _StoreAdapter(store)
        self.runs = _FakeRuns()


@pytest.fixture
async def settings() -> ReceiverSettings:
    return ReceiverSettings(
        supervisor_url="http://localhost:8000",
        supervisor_api_key="k",
        callback_token_secret=CALLBACK_TOKEN_SECRET,
        receiver_url=RECEIVER_URL,
        subagent_issuer=ISSUER,
        subagent_jwks_url="https://subagents.example/.well-known/jwks.json",
        iat_staleness_seconds=300,
    )


def _build_receiver(settings, signer, store):
    jwks_http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=signer.jwks()))
    )
    jwks = JWKSClient(settings.subagent_jwks_url, http=jwks_http)
    lg = _LG(store)
    app = create_receiver_app(
        settings=settings,
        lg=lg,
        jti_store=InMemoryJtiStore(ttl_seconds=900),
        jwks_client=jwks,
        mailbox=NotificationMailbox(lg),
    )
    return app, lg


def _emitter_http(receiver_app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=receiver_app), base_url=RECEIVER_URL)


def _build_notifier(signer, callback_token, receiver_app):
    return build_a2a_notifier_from_config(
        {
            "configurable": {
                "a2a_push_config": {
                    "url": f"{RECEIVER_URL}/a2a/notifications",
                    "token": callback_token,
                }
            }
        },
        signer=signer,
        push_client=PushClient(http=_emitter_http(receiver_app)),
    )


class TestCompletionHappyPath:
    async def test_completion_reaches_supervisor_drain(
        self, settings, signer, callback_token
    ) -> None:
        store = InMemoryStore()
        app, lg = _build_receiver(settings, signer, store)
        emitter = create_agent(
            model=GenericFakeChatModel(messages=iter(["done"])),
            tools=[],
            middleware=[_build_notifier(signer, callback_token, app)],
        )
        await emitter.ainvoke(
            {"messages": [{"role": "user", "content": "go"}]},
            config={"configurable": {"thread_id": "subagent-thread-1"}},
        )

        items = store.search(("notifications", "thread_1"))
        assert len(items) == 1
        assert items[0].key == "subagent-thread-1"
        assert items[0].value["state"] == "completed"
        assert items[0].value["iss"] == ISSUER
        assert items[0].value["summary"] == "done"
        assert [run["thread_id"] for run in lg.runs.created] == ["thread_1"]

        supervisor = create_agent(
            model=GenericFakeChatModel(messages=iter(["ok"])),
            tools=[],
            middleware=[MailboxDrainMiddleware()],
            store=store,  # compiled in: an invoke-time store does not reach the runtime
        )
        final = await supervisor.ainvoke(
            {"messages": [{"role": "user", "content": "summarize"}]},
            config={"configurable": {"thread_id": "thread_1"}},
        )
        notices = [
            m.content
            for m in final["messages"]
            if "SUBAGENT COMPLETION NOTICE" in str(getattr(m, "content", ""))
        ]
        assert len(notices) == 1
        assert "task_id: subagent-thread-1" in notices[0]
        assert "status: completed" in notices[0]
        assert store.search(("notifications", "thread_1")) == []


class TestFailedRunNotifies:
    async def test_model_crash_sends_failed_notification(
        self, settings, signer, callback_token
    ) -> None:
        store = InMemoryStore()
        app, _ = _build_receiver(settings, signer, store)
        agent = create_agent(
            model=_RaisingChatModel(),
            tools=[],
            middleware=[_build_notifier(signer, callback_token, app)],
        )
        with pytest.raises(RuntimeError, match="model crash"):
            await agent.ainvoke(
                {"messages": [{"role": "user", "content": "x"}]},
                config={"configurable": {"thread_id": "subagent-thread-2"}},
            )
        items = store.search(("notifications", "thread_1"))
        assert len(items) == 1
        assert items[0].key == "subagent-thread-2"
        assert items[0].value["state"] == "failed"


class TestDevModeKeyless:
    """The default ``dev`` tier: no keys, no JWKS -- callback token only.

    This is the trusted-private-network path the default ``A2A_VERIFY_MODE=dev``
    provides: the emitter runs without a signer and the receiver verifies
    nothing except the routing callback token.
    """

    async def test_dev_mode_keyless_completion(self, settings, callback_token, signer) -> None:
        keyless = replace(settings, subagent_jwks_url="", subagent_issuer="")
        store = InMemoryStore()
        app, _ = _build_receiver(keyless, signer, store)
        notifier = build_a2a_notifier_from_config(
            {
                "configurable": {
                    "a2a_push_config": {
                        "url": f"{RECEIVER_URL}/a2a/notifications",
                        "token": callback_token,
                    }
                }
            },
            signer=None,  # keyless emit
            push_client=PushClient(http=_emitter_http(app)),
        )
        emitter = create_agent(
            model=GenericFakeChatModel(messages=iter(["done"])),
            tools=[],
            middleware=[notifier],
        )
        await emitter.ainvoke(
            {"messages": [{"role": "user", "content": "go"}]},
            config={"configurable": {"thread_id": "subagent-thread-1"}},
        )

        items = store.search(("notifications", "thread_1"))
        assert len(items) == 1
        assert items[0].key == "subagent-thread-1"
        assert items[0].value["state"] == "completed"

        supervisor = create_agent(
            model=GenericFakeChatModel(messages=iter(["ok"])),
            tools=[],
            middleware=[MailboxDrainMiddleware()],
            store=store,
        )
        final = await supervisor.ainvoke(
            {"messages": [{"role": "user", "content": "summarize"}]},
            config={"configurable": {"thread_id": "thread_1"}},
        )
        notices = [
            m.content
            for m in final["messages"]
            if "SUBAGENT COMPLETION NOTICE" in str(getattr(m, "content", ""))
        ]
        assert len(notices) == 1
        assert "task_id: subagent-thread-1" in notices[0]
        assert store.search(("notifications", "thread_1")) == []

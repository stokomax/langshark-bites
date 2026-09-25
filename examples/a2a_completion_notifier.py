"""Runnable example for the a2a_completion_notifier bite.

Shows both halves of the split-deployment completion notifier:

1. **Emitter (subagent side)** -- an ``A2APushNotifierMiddleware`` built via
   the dynamic graph-factory pattern reads the dispatch's
   ``a2a_push_config`` and, on a terminal run state, signs and POSTs an A2A
   completion notification to the receiver webhook URL.
2. **Receiver (supervisor side)** -- a ``create_receiver_app`` FastAPI app
   receives that notification (verifying the sender's RS256 JWT against the
   JWKS endpoint), deduplicates on ``jti``, unseals the callback token, and
   delivers the completion into the supervisor's Store.  It then wakes
   the supervisor with a run carrying ``DEFAULT_WAKE_INPUT`` (a minimal nudge
   message) so the ``before_model`` drain actually fires on an idle thread.
3. **Sister result-fetch primitive** -- the supervisor asks ``POST
   /a2a/result`` for the full result of a completed task (one on-demand
   fetch; no status poll, because the notifier already proved terminality).

This example runs end-to-end without a network: the "subagent server" posts
straight to the "receiver" over ASGI, with a mock JWKS endpoint, a fake
LangGraph SDK, and a mock subagent thread-state endpoint.  Run with:

    uv run python examples/a2a_completion_notifier.py

See the docs: https://stokomax.github.io/langshark-bites/a2a_completion_notifier/
"""

from __future__ import annotations

import asyncio
import time

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from langshark_bites.a2a_completion_notifier.auth import JWKSClient
from langshark_bites.a2a_completion_notifier.drain import MailboxDrainMiddleware
from langshark_bites.a2a_completion_notifier.idempotency import InMemoryJtiStore
from langshark_bites.a2a_completion_notifier.mailbox import (
    DEFAULT_WAKE_INPUT,
    NotificationMailbox,
)
from langshark_bites.a2a_completion_notifier.middleware import (
    build_a2a_notifier_from_config,
)
from langshark_bites.a2a_completion_notifier.push_client import PushClient
from langshark_bites.a2a_completion_notifier.push_config import build_push_config
from langshark_bites.a2a_completion_notifier.receiver import create_receiver_app
from langshark_bites.a2a_completion_notifier.settings import ReceiverSettings
from langshark_bites.a2a_completion_notifier.signer import A2ASigner

RECEIVER_URL = "https://receiver.example"
ISSUER = "https://subagents.example"
KID = "subagent-key-1"
SIM_SECRET = "example-supervisor-secret"


class _FakeStore:
    """In-memory Store with both the SDK (put_item) and graph (asearch/adelete) views."""

    def __init__(self) -> None:
        self.items: list[dict] = []

    async def put_item(self, namespace, key, value) -> None:
        self.items.append({"namespace": namespace, "key": key, "value": value})

    async def asearch(self, namespace, limit=None):
        return [
            type("Item", (), {"key": item["key"], "value": item["value"]})()
            for item in self.items
            if item["namespace"] == namespace
        ][:limit]

    async def adelete(self, namespace, key) -> None:
        self.items = [
            item
            for item in self.items
            if not (item["namespace"] == namespace and item["key"] == key)
        ]


class _FakeRuns:
    async def list(self, thread_id, *, status, limit):
        return []  # supervisor idle -> wake

    async def create(self, **kwargs: object) -> None:
        # The wake run carries DEFAULT_WAKE_INPUT (a minimal nudge message): a
        # run with no input is a silent no-op on an idle thread, so the
        # before_model drain never fires.  The drain middleware injects the
        # completion notice itself; this message only makes the graph run.
        print(f"  [supervisor] wake-up run created for thread {kwargs['thread_id']}")
        print(f"  [supervisor] wake-input: {kwargs.get('input')!r}")


class _FakeLG:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.runs = _FakeRuns()


async def main() -> None:
    print("a2a_completion_notifier example\n" + "-" * 40)

    # ── Shared crypto -------------------------------------------------------
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    signer = A2ASigner(private_key_pem=pem, kid=KID, issuer=ISSUER, audience=RECEIVER_URL)

    # ── Supervisor mints the opaque callback token at dispatch time --------
    # build_push_config wraps mint_callback_token + the wire format, so a
    # supervisor graph just calls it and passes the result as `config`.
    push_config = build_push_config(
        thread_id="thread_1",
        assistant_id="supervisor_asst",
        dispatch_id="dispatch_1",
        receiver_url=RECEIVER_URL,
        callback_token_secret=SIM_SECRET,
        now=int(time.time()),
    )
    callback_token = push_config["configurable"]["a2a_push_config"]["token"]

    settings = ReceiverSettings(
        supervisor_url="http://localhost:8000",
        supervisor_api_key="supervisor-key",
        callback_token_secret=SIM_SECRET,
        receiver_url=RECEIVER_URL,
        subagent_issuer=ISSUER,
        subagent_jwks_url="https://subagents.example/.well-known/jwks.json",
        subagent_url="http://subagent-server:8000",
    )

    # ── Emitter: subagent graph marked as dispatched ----------------------
    print("[subagent] graph built for a dispatched task")
    notifier = build_a2a_notifier_from_config(
        push_config,
        signer=signer,
        push_client=PushClient(),
    )

    # Simulate hook ordering: before_agent resolves the task id...  The task id
    # resolves to the subagent *thread* id (fetchable by the supervisor's
    # full-result primitive), so thread_id is what signs/notifies with.
    runtime = type(
        "Runtime",
        (),
        {"execution_info": type("E", (), {"run_id": "run-1", "thread_id": "task-1"})()},
    )()
    await notifier.abefore_agent({}, runtime)
    # ... then the subagent run completes.
    print("[subagent] run completed; emitting A2A push notification")
    await notifier.aafter_agent({"messages": [{"role": "assistant", "content": "done"}]}, runtime)

    await receiver_flow(settings, signer, callback_token)


async def receiver_flow(settings, signer, callback_token) -> None:
    """Receiver (supervisor side) terminates the webhook and delivers."""
    from langshark_bites.a2a_completion_notifier.payload import build_notification
    from langshark_bites.a2a_completion_notifier.result_fetch import ResultFetchSettings

    print("[receiver] receiving notification over ASGI")
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=signer.jwks()))
    )
    jwks = JWKSClient(settings.subagent_jwks_url, http=http)
    lg = _FakeLG()

    # The subagent deployment serves its thread state at
    # /threads/<task_id>/state -- the sister result-fetch primitive.
    async def _state_handler(request):
        assert request.url.path == "/threads/task-1/state"
        return httpx.Response(
            200,
            json={"values": {"messages": [{"content": "full response payload"}]}},
        )

    app = create_receiver_app(
        settings=settings,
        lg=lg,
        jti_store=InMemoryJtiStore(ttl_seconds=900),
        jwks_client=jwks,
        mailbox=NotificationMailbox(lg, wake_input=DEFAULT_WAKE_INPUT),
        result_settings=ResultFetchSettings(subagent_url=settings.subagent_url),
        result_http=httpx.AsyncClient(transport=httpx.MockTransport(_state_handler)),
    )

    payload = build_notification(
        task_id="task-1", state="completed", token=callback_token, summary="done"
    )
    bearer = signer.sign("task-1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=RECEIVER_URL) as client:
        resp = await client.post(
            "/a2a/notifications",
            json=payload,
            headers={"Authorization": f"Bearer {bearer}"},
        )
        health = await client.get("/health")

    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "accepted"
    assert health.json() == {"status": "ok"}
    assert lg.store.items and lg.store.items[0]["key"] == "task-1"

    print(f"  receiver reply: {resp.json()}")
    print(f"  pending notices now hold: {lg.store.items[0]['value']}")

    # ── Drain: supervisor's before_model middleware consumes the notices ----
    # The supervisor graph attaches MailboxDrainMiddleware; on the next model
    # call it reads the pending notices from the Store and injects them.
    print("[supervisor] before_model drain hook fires")
    drain = MailboxDrainMiddleware()
    update = await drain.abefore_model(
        {},
        type(
            "Runtime",
            (),
            {
                "store": lg.store,
                "execution_info": type("E", (), {"thread_id": "thread_1"})(),
            },
        )(),
    )
    assert update is not None
    assert "task-1" in update["messages"][0].content
    assert lg.store.items == []  # notices consumed
    print(f"  injected: {update['messages'][0].content!r}")

    # ── Sister primitive: fetch the full result on demand ------------------
    # The summary is injected by the drain; when the supervisor needs the
    # full response, it asks the receiver POST /a2a/result (one call, no
    # status poll -- completion was already proven by the notifier).
    print("[supervisor] requesting the full result via /a2a/result")
    result_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=RECEIVER_URL)
    async with result_client:
        result_resp = await result_client.post("/a2a/result", json={"task_id": "task-1"})
    assert result_resp.status_code == 200, result_resp.text
    print(f"  full result: {result_resp.json()}")
    print("example OK")


if __name__ == "__main__":
    asyncio.run(main())

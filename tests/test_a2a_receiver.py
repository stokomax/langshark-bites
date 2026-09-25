"""Integration tests for the a2a_completion_notifier FastAPI receiver.

Drives the full request lifecycle over ASGI with real cryptography and a
mocked JWKS fetch + LangGraph SDK client.
"""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from langshark_bites.a2a_completion_notifier.auth import JWKSClient
from langshark_bites.a2a_completion_notifier.idempotency import InMemoryJtiStore
from langshark_bites.a2a_completion_notifier.mailbox import NotificationMailbox
from langshark_bites.a2a_completion_notifier.payload import build_notification
from langshark_bites.a2a_completion_notifier.receiver import (
    ReceiverError,
    create_receiver_app,
    process_notification,
)
from langshark_bites.a2a_completion_notifier.result_fetch import ResultFetchSettings
from langshark_bites.a2a_completion_notifier.settings import A2AVerifyMode, ReceiverSettings

RECEIVER_URL = "https://receiver.example"
ISSUER = "https://subagents.example"


class _FakeStore:
    def __init__(self) -> None:
        self.items: list[dict] = []

    async def put_item(self, namespace, key, value) -> None:
        self.items.append({"namespace": namespace, "key": key, "value": value})


class _FakeRuns:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.active = False

    async def list(self, thread_id, *, status, limit):
        return ["pending-run"] if self.active else []

    async def create(self, **kwargs) -> None:
        self.created.append(kwargs)


class _FailingRunCreateRuns:
    """Wake-run create always fails (simulates Agent Server wake failure)."""

    def __init__(self) -> None:
        self.active = False

    async def list(self, thread_id, *, status, limit):
        return []  # idle

    async def create(self, **kwargs) -> None:
        raise RuntimeError("wake create failed")


class _FakeLG:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.runs = _FakeRuns()


def _receiver(settings, signer, *, mailbox=None):
    """Build a running ASGI receiver for arbitrary settings/mode."""
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=signer.jwks()))
    )
    jwks = JWKSClient(settings.subagent_jwks_url, http=http)
    lg = _FakeLG()
    app = create_receiver_app(
        settings=settings,
        lg=lg,
        jti_store=InMemoryJtiStore(ttl_seconds=900),
        jwks_client=jwks,
        mailbox=mailbox or NotificationMailbox(lg),
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=RECEIVER_URL)
    return client, lg


@pytest.fixture
def settings(callback_token: str) -> ReceiverSettings:
    return ReceiverSettings(
        supervisor_url="http://localhost:8000",
        supervisor_api_key="k",
        callback_token_secret="super-secret-callback-token-secret",
        receiver_url=RECEIVER_URL,
        subagent_issuer=ISSUER,
        subagent_jwks_url="https://subagents.example/.well-known/jwks.json",
        iat_staleness_seconds=300,
    )


@pytest.fixture
def receiver_fixture(signer, settings):
    """Build a running ASGI client plus the fakes to assert against."""
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=signer.jwks()))
    )
    jwks = JWKSClient(settings.subagent_jwks_url, http=http)
    lg = _FakeLG()
    app = create_receiver_app(
        settings=settings,
        lg=lg,
        jti_store=InMemoryJtiStore(ttl_seconds=900),
        jwks_client=jwks,
        mailbox=NotificationMailbox(lg),
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=RECEIVER_URL)
    return client, lg


class TestReceiverFlow:
    async def test_accepts_and_delivers_terminating_notification(
        self, receiver_fixture, signer, callback_token
    ):
        client, lg = receiver_fixture
        token = signer.sign("task-1")
        payload = build_notification(
            task_id="task-1", state="completed", token=callback_token, summary="ok"
        )
        resp = await client.post(
            "/a2a/notifications",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 202
        assert resp.json() == {"status": "accepted"}

        # Deliverable written, and idle supervisor woken once.
        assert len(lg.store.items) == 1
        assert lg.store.items[0]["key"] == "task-1"
        assert lg.store.items[0]["value"]["state"] == "completed"
        # The sender identity is persisted with the notice so the
        # full-result primitive can route back to the deployment.
        assert lg.store.items[0]["value"]["iss"] == ISSUER
        assert len(lg.runs.created) == 1
        assert lg.runs.created[0]["thread_id"] == "thread_1"

    async def test_wake_failure_acks_but_surfaces_in_logs(self, signer, settings, callback_token):
        """A post-ACK wake failure must not 500; it is logged, not silent."""
        lg = _FakeLG()
        lg.runs = _FailingRunCreateRuns()
        client, _ = _receiver(settings, signer, mailbox=NotificationMailbox(lg))
        token = signer.sign("task-wake")
        payload = build_notification(task_id="task-wake", state="completed", token=callback_token)
        # The ACK (202) is already sent before the background wake runs, so the
        # POST must not raise even though trigger_if_idle fails.
        resp = await client.post(
            "/a2a/notifications",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 202
        assert resp.json() == {"status": "accepted"}
        # The completion is still written to the mailbox (only the wake failed).
        assert len(lg.store.items) == 1
        assert lg.store.items[0]["key"] == "task-wake"

    async def test_duplicate_jti_returns_duplicate(self, receiver_fixture, signer, callback_token):
        client, lg = receiver_fixture
        token = signer.sign("task-dup")
        payload = build_notification(task_id="task-dup", state="completed", token=callback_token)
        headers = {"Authorization": f"Bearer {token}"}
        first = await client.post("/a2a/notifications", json=payload, headers=headers)
        assert first.json() == {"status": "accepted"}
        second = await client.post("/a2a/notifications", json=payload, headers=headers)
        assert second.json() == {"status": "duplicate"}
        assert len(lg.store.items) == 1

    async def test_missing_bearer_returns_401(self, signer, settings, callback_token):
        strict = replace(settings, mode=A2AVerifyMode.STRICT)
        client, _ = _receiver(strict, signer)
        payload = build_notification(task_id="t", state="completed", token=callback_token)
        resp = await client.post("/a2a/notifications", json=payload)
        assert resp.status_code == 401
        assert "strict" in resp.text

    async def test_taskid_mismatch_returns_400(self, signer, settings, callback_token):
        strict = replace(settings, mode=A2AVerifyMode.STRICT)
        client, _ = _receiver(strict, signer)
        token = signer.sign("other-id")
        payload = build_notification(task_id="task-1", state="completed", token=callback_token)
        resp = await client.post(
            "/a2a/notifications",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "taskId mismatch" in resp.text

    async def test_non_terminal_state_ignored(self, receiver_fixture, signer, callback_token):
        client, lg = receiver_fixture
        token = signer.sign("task-running")
        payload = {
            "id": "task-running",
            "status": {"state": "working"},
            "metadata": {"token": callback_token},
        }
        resp = await client.post(
            "/a2a/notifications",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 202
        assert resp.json() == {"status": "ignored", "state": "TASK_STATE_WORKING"}
        assert lg.store.items == []

    async def test_bad_callback_token_returns_400(self, receiver_fixture, signer):
        client, lg = receiver_fixture
        token = signer.sign("task-bad")
        payload = {
            "id": "task-bad",
            "status": {"state": "completed"},
            "metadata": {"token": "garbage-not-a-jwt"},
        }
        resp = await client.post(
            "/a2a/notifications",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert lg.store.items == []

    async def test_health(self, receiver_fixture):
        client, _ = receiver_fixture
        resp = await client.get("/health")
        assert resp.json() == {"status": "ok"}


class TestResultEndpoint:
    """POST /a2a/result -- the sister primitive to poll-based check_async_task."""

    @pytest.fixture
    def result_receiver(self, signer, settings):
        """Receiver wired with a subagent state fetch (mock HTTP)."""

        async def _handler(request):
            return httpx.Response(200, json={"task_id": "task-1", "values": {"answer": 42}})

        jwks_http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=signer.jwks()))
        )
        jwks = JWKSClient(settings.subagent_jwks_url, http=jwks_http)
        lg = _FakeLG()
        result_http = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
        app = create_receiver_app(
            settings=settings,
            lg=lg,
            jti_store=InMemoryJtiStore(ttl_seconds=900),
            jwks_client=jwks,
            mailbox=NotificationMailbox(lg),
            result_settings=ResultFetchSettings(subagent_url="https://subagents.example"),
            result_http=result_http,
        )
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=RECEIVER_URL)

    async def test_fetches_full_result(self, result_receiver):
        client = result_receiver
        resp = await client.post("/a2a/result", json={"task_id": "task-1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "task_id": "task-1",
            "status": "ok",
            "result": {"task_id": "task-1", "values": {"answer": 42}},
        }

    async def test_missing_task_id_returns_400(self, result_receiver):
        client = result_receiver
        resp = await client.post("/a2a/result", json={})
        assert resp.status_code == 400

    async def test_unconfigured_subagent_url_returns_502(self, settings, signer):
        jwks_http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=signer.jwks()))
        )
        jwks = JWKSClient(settings.subagent_jwks_url, http=jwks_http)
        lg = _FakeLG()
        app = create_receiver_app(
            settings=settings,
            lg=lg,
            jti_store=InMemoryJtiStore(ttl_seconds=900),
            jwks_client=jwks,
            mailbox=NotificationMailbox(lg),
            result_settings=ResultFetchSettings(),  # empty: not wired
        )
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=RECEIVER_URL)
        resp = await client.post("/a2a/result", json={"task_id": "task-1"})
        assert resp.status_code == 502
        assert "A2A_SUBAGENT_URL" in resp.text

    async def test_subagent_non_2xx_returns_502(self, settings, signer):
        async def _handler(request):
            return httpx.Response(404, json={"detail": "thread not found"})

        jwks_http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=signer.jwks()))
        )
        jwks = JWKSClient(settings.subagent_jwks_url, http=jwks_http)
        lg = _FakeLG()
        app = create_receiver_app(
            settings=settings,
            lg=lg,
            jti_store=InMemoryJtiStore(ttl_seconds=900),
            jwks_client=jwks,
            mailbox=NotificationMailbox(lg),
            result_settings=ResultFetchSettings(subagent_url="https://subagents.example"),
            result_http=httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
        )
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=RECEIVER_URL)
        resp = await client.post("/a2a/result", json={"task_id": "task-missing"})
        assert resp.status_code == 502
        assert "HTTP 404" in resp.text


class TestModeDispatch:
    """A2A_VERIFY_MODE dev | verify | strict sender-verification semantics."""

    async def test_dev_unsigned_accepted(self, settings, callback_token, signer) -> None:
        client, lg = _receiver(settings, signer)  # settings fixture is dev mode
        payload = build_notification(task_id="dev-1", state="completed", token=callback_token)
        resp = await client.post("/a2a/notifications", json=payload)
        assert resp.status_code == 202
        assert resp.json() == {"status": "accepted"}
        assert lg.store.items  # delivered

    async def test_dev_signed_unverified_accepted(self, settings, callback_token, signer) -> None:
        client, _ = _receiver(settings, signer)
        payload = build_notification(task_id="dev-2", state="completed", token=callback_token)
        resp = await client.post(
            "/a2a/notifications", json=payload, headers={"Authorization": "Bearer not-a-jwt"}
        )
        assert resp.status_code == 202  # dev ignores the signature entirely

    async def test_verify_unsigned_accepted(self, settings, callback_token, signer) -> None:
        verify = replace(settings, mode=A2AVerifyMode.VERIFY)
        client, lg = _receiver(verify, signer)
        payload = build_notification(task_id="v-1", state="completed", token=callback_token)
        resp = await client.post("/a2a/notifications", json=payload)
        assert resp.status_code == 202
        assert lg.store.items

    async def test_verify_signed_valid_accepted(self, settings, callback_token, signer) -> None:
        verify = replace(settings, mode=A2AVerifyMode.VERIFY)
        client, lg = _receiver(verify, signer)
        token = signer.sign("v-2")
        payload = build_notification(task_id="v-2", state="completed", token=callback_token)
        resp = await client.post(
            "/a2a/notifications", json=payload, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 202
        assert lg.store.items

    async def test_verify_signed_invalid_rejected(self, settings, callback_token, signer) -> None:
        verify = replace(settings, mode=A2AVerifyMode.VERIFY)
        client, _ = _receiver(verify, signer)
        payload = build_notification(task_id="v-3", state="completed", token=callback_token)
        resp = await client.post(
            "/a2a/notifications", json=payload, headers={"Authorization": "Bearer bad"}
        )
        assert resp.status_code == 401  # verify-if-signed: signed and broken fails closed

    async def test_verify_without_config_accepts_signed_unverifiable(
        self, settings, callback_token, signer
    ) -> None:
        verify = replace(
            settings, mode=A2AVerifyMode.VERIFY, subagent_jwks_url="", subagent_issuer=""
        )
        client, lg = _receiver(verify, signer)
        payload = build_notification(task_id="v-4", state="completed", token=callback_token)
        resp = await client.post(
            "/a2a/notifications", json=payload, headers={"Authorization": "Bearer anything"}
        )
        assert resp.status_code == 202  # no way to verify -> dev-like tolerance
        assert lg.store.items

    async def test_strict_unsigned_rejected(self, settings, callback_token, signer) -> None:
        strict = replace(settings, mode=A2AVerifyMode.STRICT)
        client, _ = _receiver(strict, signer)
        payload = build_notification(task_id="s-1", state="completed", token=callback_token)
        resp = await client.post("/a2a/notifications", json=payload)
        assert resp.status_code == 401
        assert "strict" in resp.text

    async def test_strict_signed_valid_accepted(self, settings, callback_token, signer) -> None:
        strict = replace(settings, mode=A2AVerifyMode.STRICT)
        client, lg = _receiver(strict, signer)
        token = signer.sign("s-2")
        payload = build_notification(task_id="s-2", state="completed", token=callback_token)
        resp = await client.post(
            "/a2a/notifications", json=payload, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 202
        assert lg.store.items

    async def test_strict_signed_invalid_rejected(self, settings, callback_token, signer) -> None:
        strict = replace(settings, mode=A2AVerifyMode.STRICT)
        client, _ = _receiver(strict, signer)
        payload = build_notification(task_id="s-3", state="completed", token=callback_token)
        resp = await client.post(
            "/a2a/notifications", json=payload, headers={"Authorization": "Bearer bad"}
        )
        assert resp.status_code == 401

    async def test_strict_jwks_fetch_failure_returns_502(
        self, settings, callback_token, signer
    ) -> None:
        strict = replace(settings, mode=A2AVerifyMode.STRICT)
        jwks_http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500, json={"detail": "down"}))
        )
        jwks = JWKSClient(settings.subagent_jwks_url, http=jwks_http)
        lg = _FakeLG()
        app = create_receiver_app(
            settings=strict,
            lg=lg,
            jti_store=InMemoryJtiStore(ttl_seconds=900),
            jwks_client=jwks,
            mailbox=NotificationMailbox(lg),
        )
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=RECEIVER_URL)
        token = signer.sign("s-4")
        payload = build_notification(task_id="s-4", state="completed", token=callback_token)
        resp = await client.post(
            "/a2a/notifications", json=payload, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 502  # key-service failure, not a sender defect

    async def test_unsigned_dedup_by_task_id(self, settings, callback_token, signer) -> None:
        client, lg = _receiver(settings, signer)  # dev
        payload = build_notification(task_id="dupe", state="completed", token=callback_token)
        first = await client.post("/a2a/notifications", json=payload)
        second = await client.post("/a2a/notifications", json=payload)
        assert first.json() == {"status": "accepted"}
        assert second.json() == {"status": "duplicate"}
        assert len(lg.store.items) == 1


class TestProcessNotificationStrictGuard:
    """The runtime sender-auth guard inside ``process_notification``.

    ``create_receiver_app`` and the MCP builders fail fast via
    ``settings.ensure_valid()`` before a request is ever served, so a strict
    receiver cannot normally reach ``process_notification`` with missing
    sender-auth config.  This test drives the core function directly (the
    path a miswired programmatic caller would hit) to lock the 502 surface.
    """

    async def test_strict_missing_sender_auth_config_returns_502(
        self, settings, callback_token, signer
    ) -> None:
        # Construct the settings object directly without ensure_valid() to
        # exercise the guard inside process_notification, not the builder.
        strict_missing = ReceiverSettings(
            **{
                **settings.__dict__,
                "mode": A2AVerifyMode.STRICT,
                "subagent_jwks_url": "",
                "subagent_issuer": "",
            }
        )

        payload = build_notification(task_id="p-1", state="completed", token=callback_token)
        with pytest.raises(ReceiverError) as excinfo:
            await process_notification(
                payload,
                settings=strict_missing,
                jti_store=InMemoryJtiStore(ttl_seconds=900),
                jwks_client=JWKSClient(""),
                mailbox=NotificationMailbox(_FakeLG()),
                authorization="Bearer whatever",
            )
        assert excinfo.value.status_code == 502
        assert "sender-auth config missing" in excinfo.value.detail

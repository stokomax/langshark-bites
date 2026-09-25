"""Tests for langshark_bites.a2a_completion_notifier.push_client.

The emitter's delivery path must retry with backoff, never raise into the
agent loop, and only close an HTTP client it owns.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx

from langshark_bites.a2a_completion_notifier.push_client import (
    PushClient,
    PushClientSettings,
)

URL = "https://receiver.example/a2a/notifications"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestSendSuccess:
    async def test_returns_true_on_first_2xx(self) -> None:
        pc = PushClient(http=_client(lambda r: httpx.Response(202, json={})))
        assert await pc.send(URL, bearer="b1", payload={"id": "t"}) is True

    async def test_sends_bearer_and_json_payload(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = request.content
            return httpx.Response(200, json={})

        pc = PushClient(http=_client(handler))
        await pc.send(URL, bearer="tok-1", payload={"id": "t", "status": {"state": "completed"}})
        assert captured["auth"] == "Bearer tok-1"
        assert b'"id":"t"' in captured["body"]

    async def test_returns_true_on_2xx_after_non_2xx(self, mocker) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return (
                httpx.Response(503, json={"detail": "busy"})
                if calls["n"] == 1
                else httpx.Response(202, json={"status": "accepted"})
            )

        sleep = mocker.patch(
            "langshark_bites.a2a_completion_notifier.push_client.asyncio.sleep",
            new_callable=AsyncMock,
        )
        pc = PushClient(http=_client(handler))
        assert await pc.send(URL, bearer="b1", payload={}) is True
        sleep.assert_awaited_once_with(1.0)  # base * 2**0


class TestSendFailure:
    async def test_all_non_2xx_returns_false_and_retries_exponentially(self, mocker) -> None:
        sleep = mocker.patch(
            "langshark_bites.a2a_completion_notifier.push_client.asyncio.sleep",
            new_callable=AsyncMock,
        )
        pc = PushClient(
            http=_client(lambda r: httpx.Response(500, json={"detail": "boom"})),
            settings=PushClientSettings(max_retries=3, retry_backoff_seconds=1.0),
        )
        assert await pc.send(URL, bearer="b1", payload={}) is False
        assert [c.args[0] for c in sleep.await_args_list] == [1.0, 2.0, 4.0]

    async def test_http_error_treated_as_failed_attempt(self, mocker) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        sleep = mocker.patch(
            "langshark_bites.a2a_completion_notifier.push_client.asyncio.sleep",
            new_callable=AsyncMock,
        )
        pc = PushClient(http=_client(handler), settings=PushClientSettings(max_retries=1))
        assert await pc.send(URL, bearer="b1", payload={}) is False
        assert len(sleep.await_args_list) == 1

    async def test_never_raises_even_when_all_attempts_fail(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        pc = PushClient(
            http=_client(handler),
            settings=PushClientSettings(max_retries=2, retry_backoff_seconds=0.0),
        )
        assert await pc.send(URL, bearer="b1", payload={}) is False

    async def test_zero_retries_means_single_attempt(self, mocker) -> None:
        sleep = mocker.patch(
            "langshark_bites.a2a_completion_notifier.push_client.asyncio.sleep",
            new_callable=AsyncMock,
        )
        pc = PushClient(
            http=_client(lambda r: httpx.Response(503, json={})),
            settings=PushClientSettings(max_retries=0),
        )
        assert await pc.send(URL, bearer="b", payload={}) is False
        sleep.assert_not_awaited()


class TestAclose:
    async def test_closes_owned_http_client(self) -> None:
        pc = PushClient()
        await pc.aclose()
        assert pc._http.is_closed

    async def test_leaves_injected_client_open(self) -> None:
        client = _client(lambda r: httpx.Response(200, json={}))
        pc = PushClient(http=client)
        await pc.aclose()
        assert not client.is_closed


class TestUnsignedSend:
    async def test_bearer_none_omits_authorization_header(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(202, json={})

        pc = PushClient(http=_client(handler))
        assert await pc.send(URL, bearer=None, payload={"id": "t"}) is True
        assert captured["auth"] is None

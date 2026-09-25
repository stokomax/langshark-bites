"""Tests for langshark_bites.a2a_completion_notifier.result_fetch.

Direct coverage of the fetch-only primitive: URL construction, auth header,
and every failure branch (unconfigured, network error, non-2xx).
"""

from __future__ import annotations

import httpx
import pytest

from langshark_bites.a2a_completion_notifier.result_fetch import (
    ResultFetchSettings,
    TaskResultError,
    fetch_task_result,
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _fetch(task_id: str, *, settings: ResultFetchSettings, handler) -> dict:
    return await fetch_task_result(task_id, settings=settings, http=_client(handler))


class TestFetchSuccess:
    async def test_success_returns_json_and_strips_trailing_slash(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/threads/task-1/state"
            return httpx.Response(200, json={"values": {"answer": 42}})

        result = await _fetch(
            "task-1",
            settings=ResultFetchSettings(subagent_url="https://sub.example/"),
            handler=handler,
        )
        assert result == {"values": {"answer": 42}}

    async def test_sends_bearer_when_api_key_configured(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == "Bearer sub-key"
            return httpx.Response(200, json={})

        await _fetch(
            "task-1",
            settings=ResultFetchSettings(
                subagent_url="https://sub.example", subagent_api_key="sub-key"
            ),
            handler=handler,
        )

    async def test_quotes_special_characters_in_task_id(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert "task%2Fwith%2Fslashes%20and%20spaces" in str(request.url)
            return httpx.Response(200, json={})

        await _fetch(
            "task/with/slashes and spaces",
            settings=ResultFetchSettings(subagent_url="https://sub.example"),
            handler=handler,
        )


class TestFetchFailures:
    async def test_unconfigured_url_raises(self) -> None:
        with pytest.raises(TaskResultError, match="A2A_SUBAGENT_URL"):
            await _fetch(
                "task-1",
                settings=ResultFetchSettings(),
                handler=lambda r: httpx.Response(200, json={}),
            )

    async def test_network_error_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(TaskResultError, match="result fetch failed"):
            await _fetch(
                "task-1",
                settings=ResultFetchSettings(subagent_url="https://sub.example"),
                handler=handler,
            )

    async def test_non_2xx_raises_with_status(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "not found"})

        with pytest.raises(TaskResultError, match="HTTP 404"):
            await _fetch(
                "task-missing",
                settings=ResultFetchSettings(subagent_url="https://sub.example"),
                handler=handler,
            )

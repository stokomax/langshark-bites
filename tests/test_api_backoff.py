"""Tests for langshark_bites.api_backoff.async_backoff.

Verifies the documented public API: it sleeps `delay_sec` and logs a
WARNING when the delay is at or above `warning_threshold_sec`. The real
sleep and logger are mocked so the tests are fast and deterministic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from langshark_bites.api_backoff import async_backoff, retry_after_seconds


@pytest.mark.asyncio
@patch("langshark_bites.api_backoff.asyncio.sleep", new_callable=AsyncMock)
@patch("langshark_bites.api_backoff.log.warning")
async def test_sleeps_for_delay_and_no_warning_below_threshold(mock_warning, mock_sleep):
    """Delay below the threshold sleeps but does not log a warning."""
    await async_backoff(0.5, context="NewsAPI retry 1/3")

    mock_sleep.assert_awaited_once_with(0.5)
    mock_warning.assert_not_called()


@pytest.mark.asyncio
@patch("langshark_bites.api_backoff.asyncio.sleep", new_callable=AsyncMock)
@patch("langshark_bites.api_backoff.log.warning")
async def test_logs_warning_when_delay_meets_threshold(mock_warning, mock_sleep):
    """A delay at or above the threshold logs the documented warning."""
    await async_backoff(10.0, context="LLM:deepseek-chat retry 1/3")

    mock_sleep.assert_awaited_once_with(10.0)
    mock_warning.assert_called_once_with(
        "rate_limit_backoff",
        delay_sec=10.0,
        context="LLM:deepseek-chat retry 1/3",
    )


@pytest.mark.asyncio
@patch("langshark_bites.api_backoff.asyncio.sleep", new_callable=AsyncMock)
@patch("langshark_bites.api_backoff.log.warning")
async def test_logs_warning_when_delay_exceeds_threshold(mock_warning, mock_sleep):
    """A delay above the threshold logs a warning, with delay_sec rounded."""
    await async_backoff(0.26, warning_threshold_sec=0.1, context="test")

    mock_sleep.assert_awaited_once_with(0.26)
    mock_warning.assert_called_once_with(
        "rate_limit_backoff",
        delay_sec=0.3,  # round(delay_sec, 1)
        context="test",
    )


@pytest.mark.asyncio
@patch("langshark_bites.api_backoff.asyncio.sleep", new_callable=AsyncMock)
@patch("langshark_bites.api_backoff.log.warning")
async def test_context_defaults_to_empty(mock_warning, mock_sleep):
    """context is optional and defaults to an empty string."""
    await async_backoff(10.0)

    mock_sleep.assert_awaited_once_with(10.0)
    mock_warning.assert_called_once_with(
        "rate_limit_backoff",
        delay_sec=10.0,
        context="",
    )


# ---------------------------------------------------------------------------
# retry_after_seconds
# ---------------------------------------------------------------------------


class _Resp:
    """Minimal stand-in with a .headers mapping."""

    def __init__(self, headers):
        self.headers = headers


def test_retry_after_integer_seconds():
    r = _Resp({"Retry-After": "30"})
    assert retry_after_seconds(r) == 30.0


def test_retry_after_missing_header_returns_none():
    assert retry_after_seconds(_Resp({})) is None


def test_retry_after_unparseable_returns_none():
    r = _Resp({"Retry-After": "sometime later"})
    assert retry_after_seconds(r) is None


def test_retry_after_http_date():
    from datetime import datetime, timedelta, timezone

    retry_at = datetime.now(timezone.utc) + timedelta(seconds=60)
    r = _Resp(
        {"Retry-After": retry_at.strftime("%a, %d %b %Y %H:%M:%S GMT")}
    )
    secs = retry_after_seconds(r)
    assert secs is not None
    assert 55.0 <= secs <= 65.0


def test_retry_after_object_without_headers():
    class NoHeaders:
        pass

    assert retry_after_seconds(NoHeaders()) is None

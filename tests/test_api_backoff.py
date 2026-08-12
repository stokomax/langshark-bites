"""Tests for langshark_bites.api_backoff.async_backoff.

Verifies the documented public API: it sleeps `delay_sec` and logs a
WARNING when the delay is at or above `warning_threshold_sec`. The real
sleep and logger are mocked so the tests are fast and deterministic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from langshark_bites.api_backoff import async_backoff


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

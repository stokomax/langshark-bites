"""Unit tests for langshark_bites.observability.phoenix (Phoenix / OpenInference helpers).

No live Phoenix required — tests cover no-op behaviour and decorator wiring
with a mock tracer.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from langshark_bites.observability import (
    agent_span,
    chain_span,
    phoenix_get_tracer,
    phoenix_is_initialized,
    tool_span,
)
from langshark_bites.observability.phoenix import setup
from langshark_bites.observability.phoenix.setup import _reset_for_tests, init_phoenix


@pytest.fixture(autouse=True)
def _clean_phoenix_state():
    """Reset module state before/after every test."""
    _reset_for_tests()
    yield
    _reset_for_tests()


# ---------------------------------------------------------------------------
# No-op path (Phoenix not initialized)
# ---------------------------------------------------------------------------


class TestNoOpDecorators:
    def test_agent_span_sync_passthrough(self):
        calls: list[str] = []

        @agent_span(default_override="my-agent")
        def work(x: int) -> int:
            calls.append("ran")
            return x * 2

        assert work(3) == 6
        assert calls == ["ran"]
        assert phoenix_get_tracer() is None
        assert phoenix_is_initialized() is False

    @pytest.mark.asyncio
    async def test_agent_span_async_passthrough(self):
        @agent_span
        async def work(x: int) -> int:
            return x + 1

        assert await work(10) == 11

    def test_chain_and_tool_noop(self):
        @chain_span
        def c() -> str:
            return "c"

        @tool_span
        def t() -> str:
            return "t"

        assert c() == "c"
        assert t() == "t"


# ---------------------------------------------------------------------------
# init_phoenix
# ---------------------------------------------------------------------------


class TestInitPhoenix:
    def test_soft_fail_marks_initialized(self):
        """Import failure must not crash; marks initialized so we don't retry."""
        real_import = __import__

        def selective_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "phoenix.otel" or name.startswith("phoenix.otel"):
                raise ImportError("no phoenix")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=selective_import):
            result = init_phoenix(endpoint="http://x", project_name="p")

        assert result is None
        assert phoenix_is_initialized() is True
        assert phoenix_get_tracer() is None

        # Second call is a no-op (idempotent)
        assert init_phoenix(endpoint="http://x", project_name="p") is None

    def test_successful_register(self):
        mock_tracer = MagicMock(name="OITracer")
        mock_provider = MagicMock()
        mock_provider.get_tracer.return_value = mock_tracer

        mock_otel = MagicMock()
        mock_otel.register.return_value = mock_provider

        with patch.dict("sys.modules", {"phoenix.otel": mock_otel}):
            result = init_phoenix(
                endpoint="http://localhost:6006",
                project_name="test",
                auto_instrument=True,
            )

        assert result is mock_provider
        assert phoenix_is_initialized() is True
        assert phoenix_get_tracer() is mock_tracer
        mock_otel.register.assert_called_once()
        kwargs = mock_otel.register.call_args.kwargs
        assert kwargs["endpoint"] == "http://localhost:6006"
        assert kwargs["project_name"] == "test"
        assert kwargs["auto_instrument"] is True


# ---------------------------------------------------------------------------
# Decorators with mock tracer
# ---------------------------------------------------------------------------


class TestDecoratorsWithTracer:
    @pytest.fixture
    def mock_tracer(self):
        """Install a mock OITracer into module state."""
        spans: list[dict[str, Any]] = []

        @contextmanager
        def start_as_current_span(name: str, **kwargs: Any):
            spans.append({"name": name, **kwargs})
            yield MagicMock(name=f"span:{name}")

        tracer = MagicMock()
        tracer.start_as_current_span = start_as_current_span

        setup._initialized = True
        setup._tracer_provider = MagicMock()
        setup._default_tracer = tracer

        return tracer, spans

    def test_agent_span_creates_agent_kind(self, mock_tracer):
        _, spans = mock_tracer

        @agent_span(default_override="subagent")
        def work(x: int) -> int:
            return x

        assert work(5) == 5
        assert len(spans) == 1
        assert spans[0]["name"] == "subagent"
        assert spans[0]["openinference_span_kind"] == "agent"

    @pytest.mark.asyncio
    async def test_agent_span_async_with_tags(self, mock_tracer):
        _, spans = mock_tracer
        meta_seen: list[dict[str, Any]] = []

        @contextmanager
        def fake_using_attributes(*, metadata=None, **_k: Any):
            if metadata:
                meta_seen.append(metadata)
            yield

        with patch("phoenix.otel.using_attributes", fake_using_attributes):

            @agent_span(
                default_override="subagent",
                tags={"agent": "daily_signal_analysis", "as_of": "2026-07-10"},
            )
            async def run_worker(agent_name: str, as_of: str) -> str:
                return f"{agent_name}:{as_of}"

            result = await run_worker("daily_signal_analysis", "2026-07-10")

        assert result == "daily_signal_analysis:2026-07-10"
        assert len(spans) == 1
        assert spans[0]["openinference_span_kind"] == "agent"
        assert meta_seen == [
            {"agent": "daily_signal_analysis", "as_of": "2026-07-10"}
        ]

    def test_chain_and_tool_kinds(self, mock_tracer):
        _, spans = mock_tracer

        @chain_span(default_override="router")
        def route() -> str:
            return "ok"

        @tool_span(default_override="my-tool")
        def tool() -> str:
            return "tool"

        assert route() == "ok"
        assert tool() == "tool"
        kinds = [s["openinference_span_kind"] for s in spans]
        assert kinds == ["chain", "tool"]

    @pytest.mark.asyncio
    async def test_parse_agent_name(self, mock_tracer):
        """parse_agent_name=True names the span after the agent_name value."""
        _, spans = mock_tracer

        @agent_span(parse_agent_name=True)
        async def run_worker(agent_name: str, as_of: str) -> str:
            return f"{agent_name}:{as_of}"

        await run_worker("daily_signal_analysis", "2026-07-10")

        assert spans[0]["name"] == "daily_signal_analysis"
        assert spans[0]["openinference_span_kind"] == "agent"

    @pytest.mark.asyncio
    async def test_parse_agent_name_keyword_call(self, mock_tracer):
        """parse_agent_name works even when called with kwargs."""
        _, spans = mock_tracer

        @agent_span(parse_agent_name=True)
        async def run(agent_name: str, as_of: str) -> str:
            return as_of

        await run(agent_name="macro_analysis", as_of="2026-01-01")

        assert spans[0]["name"] == "macro_analysis"

    def test_default_override_literal(self, mock_tracer):
        """A fixed default_override is used verbatim."""
        _, spans = mock_tracer

        @agent_span(default_override="my-supervisor")
        def run(agent_name: str) -> str:
            return agent_name

        run("anything")
        assert spans[0]["name"] == "my-supervisor"

    def test_name_none_uses_function_name(self, mock_tracer):
        """With no override/parse flag, the span uses the function name."""
        _, spans = mock_tracer

        @agent_span
        def run(agent_name: str) -> str:
            return agent_name

        run("x")
        assert spans[0]["name"] == "run"

    @pytest.mark.asyncio
    async def test_parse_agent_name_falls_back_to_function_name(self, mock_tracer):
        """parse_agent_name=True with no agent_name param falls back to fn name."""
        _, spans = mock_tracer

        @agent_span(parse_agent_name=True)
        async def run_worker(x: int) -> int:
            return x

        await run_worker(5)
        assert spans[0]["name"] == "run_worker"

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
    get_tracer,
    is_initialized,
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

        @agent_span(name="my-agent")
        def work(x: int) -> int:
            calls.append("ran")
            return x * 2

        assert work(3) == 6
        assert calls == ["ran"]
        assert get_tracer() is None
        assert is_initialized() is False

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

    def test_metadata_fn_not_called_when_uninitialized(self):
        meta_calls = 0

        def meta_fn(*_a: Any, **_k: Any) -> dict[str, Any]:
            nonlocal meta_calls
            meta_calls += 1
            return {"k": "v"}

        @agent_span(metadata_fn=meta_fn)
        def work() -> int:
            return 1

        assert work() == 1
        assert meta_calls == 0  # short-circuit before metadata_fn


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
        assert is_initialized() is True
        assert get_tracer() is None

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
        assert is_initialized() is True
        assert get_tracer() is mock_tracer
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

        @agent_span(name="subagent")
        def work(x: int) -> int:
            return x

        assert work(5) == 5
        assert len(spans) == 1
        assert spans[0]["name"] == "subagent"
        assert spans[0]["openinference_span_kind"] == "agent"

    @pytest.mark.asyncio
    async def test_agent_span_async_with_metadata(self, mock_tracer):
        _, spans = mock_tracer
        meta_seen: list[dict[str, Any]] = []

        def meta_fn(agent_name: str, as_of: str, **_: Any) -> dict[str, Any]:
            return {"agent": agent_name, "as_of": as_of}

        @contextmanager
        def fake_using_attributes(*, metadata=None, **_k: Any):
            if metadata:
                meta_seen.append(metadata)
            yield

        with patch("phoenix.otel.using_attributes", fake_using_attributes):

            @agent_span(name="subagent", metadata_fn=meta_fn)
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

        @chain_span(name="router")
        def route() -> str:
            return "ok"

        @tool_span(name="my-tool")
        def tool() -> str:
            return "tool"

        assert route() == "ok"
        assert tool() == "tool"
        kinds = [s["openinference_span_kind"] for s in spans]
        assert kinds == ["chain", "tool"]

    def test_metadata_fn_exception_is_swallowed(self, mock_tracer):
        _, spans = mock_tracer

        def bad_meta(*_a: Any, **_k: Any) -> dict[str, Any]:
            raise RuntimeError("boom")

        @agent_span(metadata_fn=bad_meta)
        def work() -> int:
            return 42

        assert work() == 42
        assert len(spans) == 1  # span still created

    @pytest.mark.asyncio
    async def test_agent_name_param_selector(self, mock_tracer):
        """Regression: name=<param> must name the span after that param's value.

        Mirrors the ``_invoke_worker_traced`` pattern in supervisor graphs,
        which uses ``@agent_span(name="agent_name")``.  Without signature-bound
        resolution, the span would be named after the wrapped function
        (``_invoke_worker_traced``) instead of the agent.
        """
        _, spans = mock_tracer

        @agent_span(name="agent_name")
        async def _invoke_worker_traced(
            agent_name: str, as_of: str, task_id: str, description: str, config: Any
        ) -> str:
            return f"{agent_name}:{as_of}"

        # Positional invocation — must still resolve by parameter name.
        result = await _invoke_worker_traced(
            "daily_signal_analysis", "2026-07-10", "x:2026-07-10", "desc", {}
        )

        assert result == "daily_signal_analysis:2026-07-10"
        assert len(spans) == 1
        assert spans[0]["name"] == "daily_signal_analysis"
        assert spans[0]["openinference_span_kind"] == "agent"

    @pytest.mark.asyncio
    async def test_agent_name_param_selector_keyword_call(self, mock_tracer):
        """Param selector works even when the function is called with kwargs."""
        _, spans = mock_tracer

        @agent_span(name="agent_name")
        async def run(agent_name: str, as_of: str) -> str:
            return as_of

        await run(agent_name="macro_analysis", as_of="2026-01-01")

        assert spans[0]["name"] == "macro_analysis"

    def test_literal_name(self, mock_tracer):
        """A name that is not a parameter is used verbatim as a literal."""
        _, spans = mock_tracer

        @agent_span(name="my-supervisor")
        def run(agent_name: str) -> str:
            return agent_name

        run("anything")
        assert spans[0]["name"] == "my-supervisor"

    def test_name_none_uses_function_name(self, mock_tracer):
        """name=None falls back to the wrapped function's name."""
        _, spans = mock_tracer

        @agent_span
        def run(agent_name: str) -> str:
            return agent_name

        run("x")
        assert spans[0]["name"] == "run"

    @pytest.mark.asyncio
    async def test_metadata_fn_receives_bound_kwargs(self, mock_tracer):
        """metadata_fn is invoked with bound kwargs (position-independent)."""
        _, spans = mock_tracer
        meta_seen: list[dict[str, Any]] = []

        def meta_fn(agent_name: str, as_of: str, **_: Any) -> dict[str, Any]:
            return {"agent": agent_name, "as_of": as_of}

        @contextmanager
        def fake_using_attributes(*, metadata=None, **_k: Any):
            if metadata:
                meta_seen.append(metadata)
            yield

        with patch("phoenix.otel.using_attributes", fake_using_attributes):

            @agent_span(name="agent_name", metadata_fn=meta_fn)
            async def run_worker(agent_name: str, as_of: str) -> str:
                return f"{agent_name}:{as_of}"

            # Positional invocation — metadata_fn still gets named params.
            result = await run_worker("daily_signal_analysis", "2026-07-10")

        assert result == "daily_signal_analysis:2026-07-10"
        assert spans[0]["name"] == "daily_signal_analysis"
        assert meta_seen == [
            {"agent": "daily_signal_analysis", "as_of": "2026-07-10"}
        ]

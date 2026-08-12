"""OpenInference-style span decorators for agents, chains, and tools.

Standalone, extractable module.  Wraps Phoenix OITracer's
``start_as_current_span`` with:

1. **Graceful no-op** when Phoenix is not initialized (safe in unit tests).
2. **OpenInference span kinds** (agent / chain / tool) for Phoenix UI.
   OpenInference requires lowercase kind strings when passed as str.
3. **Metadata attachment** via ``using_attributes(metadata=...)`` so spans
   carry filterable keys (agent name, as_of, task_id, …).

Public API
----------
- ``agent_span(name=None, metadata_fn=None)``
- ``chain_span(name=None, metadata_fn=None)``
- ``tool_span(name=None, metadata_fn=None)``

Usage::

    from langshark_bites.observability.phoenix import agent_span

    @agent_span(name="subagent", metadata_fn=lambda agent_name, as_of, task_id, **_: {
        "agent": agent_name, "as_of": as_of, "task_id": task_id,
    })
    async def run_worker(agent_name, as_of, task_id, description, config):
        ...

These decorators are the Phoenix equivalent of LangSmith's ``@traceable``.
They are intentionally thin wrappers around OpenInference so the module can
be lifted into a shared package without application dependencies
(only ``setup.get_tracer`` from this package).
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from langshark_bites.observability.phoenix.setup import get_tracer

F = TypeVar("F", bound=Callable[..., Any])
MetadataFn = Callable[..., dict[str, Any] | None]
NameArg = str | None

# OpenInference requires lowercase when kind is provided as a string.
_KIND_AGENT = "agent"
_KIND_CHAIN = "chain"
_KIND_TOOL = "tool"


def _bound_args(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Bind the actual call args to the function's parameter names.

    Returns a ``{param_name: value}`` mapping (with defaults applied) so
    downstream name/metadata resolution is a deterministic lookup by name,
    independent of whether the caller used positional or keyword arguments.
    Returns ``{}`` if binding fails — observability must never break the app.
    """
    try:
        bound = inspect.signature(func).bind(*args, **kwargs)
        bound.apply_defaults()
        return bound.arguments
    except (TypeError, ValueError):
        return {}


def _resolve_span_name(
    name: NameArg,
    fn_name: str,
    bound: dict[str, Any],
) -> str:
    """Resolve the span name deterministically.

    Resolution order:
    1. ``name is None`` -> the wrapped function's name.
    2. ``name`` matches a bound parameter name -> that parameter's value.
       (e.g. ``@agent_span(name="agent_name")`` names the span after the
       agent at runtime, regardless of positional/keyword calling.)
    3. otherwise -> ``name`` verbatim (a literal span name).
    """
    if name is None:
        return fn_name
    if name in bound:
        value = bound[name]
        if isinstance(value, str):
            return value
        return str(value)
    return name


def agent_span(
    fn: F | None = None,
    /,
    *,
    name: NameArg | None = None,
    metadata_fn: MetadataFn | None = None,
) -> Any:
    """Decorator: OpenInference AGENT span.  No-op if Phoenix uninitialized."""
    return _make_decorator(_KIND_AGENT, fn, name=name, metadata_fn=metadata_fn)


def chain_span(
    fn: F | None = None,
    /,
    *,
    name: NameArg | None = None,
    metadata_fn: MetadataFn | None = None,
) -> Any:
    """Decorator: OpenInference CHAIN span.  No-op if Phoenix uninitialized."""
    return _make_decorator(_KIND_CHAIN, fn, name=name, metadata_fn=metadata_fn)


def tool_span(
    fn: F | None = None,
    /,
    *,
    name: NameArg | None = None,
    metadata_fn: MetadataFn | None = None,
) -> Any:
    """Decorator: OpenInference TOOL span.  No-op if Phoenix uninitialized."""
    return _make_decorator(_KIND_TOOL, fn, name=name, metadata_fn=metadata_fn)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _make_decorator(
    span_kind: str,
    fn: F | None,
    *,
    name: NameArg | None,
    metadata_fn: MetadataFn | None,
) -> Any:
    def decorator(func: F) -> F:
        is_async = inspect.iscoroutinefunction(func)

        if is_async:

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = get_tracer()
                if tracer is None:
                    return await func(*args, **kwargs)
                bound = _bound_args(func, args, kwargs)
                meta = _safe_metadata(metadata_fn, bound)
                span_name = _resolve_span_name(name, func.__name__, bound)
                return await _invoke_async(
                    tracer, span_name, span_kind, meta, func, args, kwargs
                )

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            if tracer is None:
                return func(*args, **kwargs)
            bound = _bound_args(func, args, kwargs)
            meta = _safe_metadata(metadata_fn, bound)
            span_name = _resolve_span_name(name, func.__name__, bound)
            return _invoke_sync(tracer, span_name, span_kind, meta, func, args, kwargs)

        return sync_wrapper  # type: ignore[return-value]

    if fn is not None:
        return decorator(fn)
    return decorator


def _safe_metadata(
    metadata_fn: MetadataFn | None,
    bound: dict[str, Any],
) -> dict[str, Any] | None:
    if metadata_fn is None:
        return None
    try:
        meta = metadata_fn(**bound)
    except Exception:
        return None
    if not meta:
        return None
    return {str(k): _stringify(v) for k, v in meta.items() if v is not None}


def _stringify(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _span_cm(tracer: Any, span_name: str, span_kind: str, meta: dict[str, Any] | None):
    """Build nested context managers: optional metadata + agent/chain/tool span."""
    from contextlib import ExitStack

    stack = ExitStack()
    if meta:
        try:
            from phoenix.otel import using_attributes

            stack.enter_context(using_attributes(metadata=meta))
        except ImportError:
            pass

    stack.enter_context(
        tracer.start_as_current_span(
            span_name,
            openinference_span_kind=span_kind,
        )
    )
    return stack


def _invoke_sync(
    tracer: Any,
    span_name: str,
    span_kind: str,
    meta: dict[str, Any] | None,
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    with _span_cm(tracer, span_name, span_kind, meta):
        return func(*args, **kwargs)


async def _invoke_async(
    tracer: Any,
    span_name: str,
    span_kind: str,
    meta: dict[str, Any] | None,
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    with _span_cm(tracer, span_name, span_kind, meta):
        return await func(*args, **kwargs)

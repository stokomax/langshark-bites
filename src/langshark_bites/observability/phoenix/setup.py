"""Phoenix / OpenInference tracer registration.

Standalone, extractable module.  Zero imports from application code —
callers inject endpoint/project via arguments.

Public API
----------
- ``init_phoenix(endpoint, project_name, ...)`` — idempotent, thread-safe
- ``get_tracer(name=None)`` — returns OITracer or None if not initialized
- ``is_initialized()`` — bool

Design notes
------------
- Registration is expensive (~seconds of SDK scan when auto_instrument=True)
  and must NOT run on the ASGI event loop (blockbuster rejects os.mkdir /
  heavy imports).  Callers should invoke ``init_phoenix`` via
  ``asyncio.to_thread`` or at process startup.
- If Phoenix is not installed, or ``init_phoenix`` was never called,
  ``get_tracer`` returns None and the decorators in ``traceable`` no-op.
- This module only needs ``phoenix.otel`` (register + using_attributes),
  provided by the slim ``arize-phoenix-otel`` package.  Do NOT add the full
  ``arize-phoenix`` app SDK to the agent process: it drags in
  pydantic-ai-slim → genai-prices → httpx2, which races openai>=2.53's
  ``_httpx2`` helpers ("partially initialized module 'httpx2' ... no
  attribute 'URL'").  The Phoenix UI/collector runs as its own service
  (e.g. a docker-compose ``phoenix``).
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_initialized = False
_tracer_provider: Any = None
_default_tracer: Any = None


def is_initialized() -> bool:
    """Return True if ``init_phoenix`` has completed successfully."""
    return _initialized


def init_phoenix(
    *,
    endpoint: str = "http://localhost:6006",
    project_name: str = "default",
    batch: bool = True,
    auto_instrument: bool = True,
    verbose: bool = False,
    api_key: str | None = None,
) -> Any | None:
    """Register Phoenix OTEL exporter once per process.

    Idempotent and thread-safe.  Subsequent calls are no-ops and return the
    existing tracer provider.

    Args:
        endpoint: Phoenix collector endpoint (HTTP).
        project_name: Phoenix project name for span grouping.
        batch: Use batch span processor (recommended for production).
        auto_instrument: Wire OpenInference LangChain instrumentor so LLM
            and tool calls nest under manual agent/chain spans.
        verbose: Phoenix SDK verbose logging.
        api_key: Optional Phoenix cloud API key.

    Returns:
        The TracerProvider, or None if registration failed / Phoenix missing.
    """
    global _initialized, _tracer_provider, _default_tracer

    if _initialized:
        return _tracer_provider

    with _lock:
        if _initialized:
            return _tracer_provider

        try:
            import phoenix.otel as phoenix_otel
        except ImportError:
            _initialized = True  # don't retry missing package every call
            _tracer_provider = None
            _default_tracer = None
            return None

        try:
            kwargs: dict[str, Any] = {
                "endpoint": endpoint,
                "project_name": project_name,
                "batch": batch,
                "verbose": verbose,
                "auto_instrument": auto_instrument,
            }
            if api_key:
                kwargs["api_key"] = api_key

            provider = phoenix_otel.register(**kwargs)
            _tracer_provider = provider
            _default_tracer = provider.get_tracer("observability")
            _initialized = True
            return provider
        except Exception:
            # Soft-fail: tracing must never take down the app
            _initialized = True
            _tracer_provider = None
            _default_tracer = None
            return None


def get_tracer(name: str | None = None) -> Any | None:
    """Return an OITracer, or None if Phoenix is not initialized.

    Args:
        name: Optional tracer name.  Defaults to the process-wide tracer
            created by ``init_phoenix``.
    """
    if not _initialized or _tracer_provider is None:
        return None
    if name is None:
        return _default_tracer
    try:
        return _tracer_provider.get_tracer(name)
    except Exception:
        return _default_tracer


def _reset_for_tests() -> None:
    """Clear module state — tests only."""
    global _initialized, _tracer_provider, _default_tracer
    with _lock:
        _initialized = False
        _tracer_provider = None
        _default_tracer = None

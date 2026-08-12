"""Observability helpers for LangChain / LangGraph apps.

``observability`` is a namespace for tracing backends.  Today it ships a
single concrete backend, ``phoenix``, whose public API is re-exported here so
applications can import from the top-level ``observability`` package:

    from langshark_bites.observability import init_phoenix, agent_span

Future backends (e.g. ``observability.langsmith``) can be added alongside
``phoenix`` without changing the public API.
"""

from __future__ import annotations

from langshark_bites.observability.phoenix import (
    agent_span,
    chain_span,
    get_tracer,
    init_phoenix,
    is_initialized,
    tool_span,
)

__all__ = [
    "init_phoenix",
    "get_tracer",
    "is_initialized",
    "agent_span",
    "chain_span",
    "tool_span",
]

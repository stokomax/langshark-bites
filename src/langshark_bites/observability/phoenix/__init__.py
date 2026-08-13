"""Phoenix observability: tracer setup + OpenInference span decorators.

Exposes a single, project-agnostic API for wiring Phoenix / OpenInference
into a LangChain / LangGraph app without dragging in the full ``arize-phoenix``
app SDK (see ``setup`` for the bloat rationale).

Public API
----------
- ``init_phoenix`` / ``phoenix_get_tracer`` / ``phoenix_is_initialized``
- ``agent_span`` / ``chain_span`` / ``tool_span``

Quick start
-----------
::

    from langshark_bites.observability.phoenix import (
        init_phoenix,
        agent_span,
    )

    # Once per process (off the event loop if under ASGI):
    init_phoenix(endpoint="http://localhost:6006", project_name="my-app")

    @agent_span(parse_agent_name=True, tags={"as_of": "2026-07-10"})
    async def run_worker(agent_name: str, ...):
        ...
"""

from __future__ import annotations

from langshark_bites.observability.phoenix.setup import (
    init_phoenix,
    phoenix_get_tracer,
    phoenix_is_initialized,
)
from langshark_bites.observability.phoenix.traceable import (
    agent_span,
    chain_span,
    tool_span,
)

__all__ = [
    "init_phoenix",
    "phoenix_get_tracer",
    "phoenix_is_initialized",
    "agent_span",
    "chain_span",
    "tool_span",
]

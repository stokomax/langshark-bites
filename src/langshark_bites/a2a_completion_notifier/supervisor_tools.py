"""Supervisor-side helper exposing an MCP server's tools to LangChain.

Why this exists
---------------
A supervisor deployment consumes an MCP server's tools as **native LangChain
tools** via ``langchain.mcp.MCPAdapter`` -- but the adapter must be used
**only for discovery**, not held across runs.

``as_langchain_tool`` builds tools that "open the client themselves whether
or not a connection is already held elsewhere": each invocation does its own
``async with client:`` (fastmcp reentrancy counter ``0→1→0``).  If the
adapter's client is left connected across runs (``nesting_counter`` stuck at
``1``), the **second** tool call crashes with fastmcp's *"Internal error:
nesting counter should be 0 when starting new session, got 1"*.

This helper is the canonical, regression-tested way to build the supervisor
tools: open the adapter for a short discovery scope, return the tools, close
the adapter.  Each tool reconnects per invocation.

Blockbuster-safe pre-warm
-------------------------
``mcp.client.session.call_tool`` lazily imports ``jsonschema`` (via
``validate_tool_result``) on the first tool call, and that import builds
``jsonschema_specifications``' schema registry with a synchronous directory
scan.  Inside ``langgraph dev``'s ASGI event loop, ``blockbuster`` intercepts
that scan and fails the run ("Blocking call to ScandirIterator.__next__").

The scan must therefore run at **graph-module import time**, before the event
loop (and with it blockbuster) is active -- the same pattern langgraph_api
itself uses for ddtrace (see ``langgraph_api.graph``).  Call
``prewarm_supervisor_tools()`` at the top of the graph module that will host
the supervisor tools (import it at module scope, not inside a coroutine):

    # src/.../supervisor.py
    from langshark_bites.a2a_completion_notifier.supervisor_tools import (
        prewarm_supervisor_tools,
    )
    prewarm_supervisor_tools()

Do NOT call it from inside ``create_supervisor_tools`` or any other coroutine:
under a running loop the synchronous scan trips blockbuster regardless of
which thread the loop lives in.
"""

from __future__ import annotations

import contextlib
import importlib
from typing import Any, Literal

__all__ = ["create_supervisor_tools", "prewarm_supervisor_tools"]


def prewarm_supervisor_tools() -> None:
    """Import everything the MCP tool path touches lazily, at module import.

    ``langgraph dev``'s blockbuster rejects blocking file IO inside any running
    loop ("Blocking call to ...").  Several lazy imports on the MCP discovery /
    tool-call path would otherwise do a blocking read at the worst time:

    - ``mcp.client.session.validate_tool_result`` lazily imports ``jsonschema``,
      whose ``jsonschema_specifications`` registry scans a directory;
    - ``langchain.mcp`` (first import) pulls ``fastmcp`` + ``pydantic_settings``,
      which read ``.env``;
    - fastmcp's HTTP transport lazily imports ``httpx2``/``httpcore2`` at
      connect time, whose ``importlib.metadata`` read pulls a package METADATA
      file.

    Call this at the top level of the graph module that hosts the supervisor's
    MCP tools -- before the event loop / blockbuster exist -- so every one of
    those imports is a no-op cache hit during discovery and per-call
    reconnects.  Exactly like ``langgraph_api.graph`` eagerly imports ddtrace.
    Never call it from inside a coroutine (a running loop makes the scan fatal).
    """
    # Imported purely for the module-load side effect (filesystem scans, .env
    # reads).  importlib keeps the names off the local namespace so ruff does
    # not flag them as unused, while still running the same blocking scans at
    # graph-module import time (before blockbuster is active).
    for _mod in (
        "jsonschema.exceptions",
        "jsonschema.validators",
        "jsonschema_specifications",
        "langchain.mcp",
    ):
        importlib.import_module(_mod)

    for _mod in ("httpcore2", "httpx2"):  # optional HTTP MCP transport stack
        with contextlib.suppress(ImportError):  # pragma: no cover - env-dependent
            importlib.import_module(_mod)


async def create_supervisor_tools(
    target: Any,
    *,
    cache_mode: Literal["use", "refresh", "bypass"] = "use",
) -> list[Any]:
    """Discover an MCP server's tools as reconnectable LangChain tools.

    Opens ``langchain.mcp.MCPAdapter`` for a short discovery scope and closes
    it before returning.  Each returned tool holds the client and reconnects
    per invocation (the ``as_langchain_tool`` contract), so consecutive calls
    -- including to the SAME tool on the SAME server -- cannot trip fastmcp's
    reentrancy-nesting crash.

    The mcp SDK's lazy ``jsonschema`` import must already be warmed BEFORE any
    coroutine runs: call ``prewarm_supervisor_tools()`` at module import (see
    the module docstring).  This function deliberately does not import
    jsonschema itself -- inside a running loop the synchronous scan would trip
    blockbuster.

    Args:
        target: MCP target accepted by ``langchain.mcp.MCPAdapter`` -- a URL
            string (``http(s)://.../mcp``) or an ``mcpServers`` dict for
            stdio / streamable-http.
        cache_mode: How discovery reads the client-side response cache
            (SEP-2549).  Defaults to ``"use"`` so a configured cache is
            honored, matching ``MCPAdapter.list_tools``.

    Returns:
        The server's tools as LangChain ``BaseTool`` objects.

    Raises:
        ImportError: If ``langchain.mcp`` is not installed (add the
            ``langchain[mcp]`` extra).
    """
    try:
        from langchain.mcp import MCPAdapter
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise ImportError(
            "create_supervisor_tools requires langchain[mcp] "
            "(install langchain's MCP extra) to build LangChain tools."
        ) from exc

    async with MCPAdapter(target) as adapter:
        return await adapter.list_tools(cache_mode=cache_mode)

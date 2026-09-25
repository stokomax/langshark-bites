"""Regression test: ``create_supervisor_tools`` returns reconnectable tools.

A supervisor consumes an MCP server's tools via ``langchain.mcp.MCPAdapter``.
The adapter must be used only for discovery; each tool reconnects per
invocation.  Holding the adapter open across runs leaves fastmcp's
nested-session counter stuck at ``1`` and crashes the SECOND tool call with:

    RuntimeError: Internal error: nesting counter should be 0 when starting
    new session, got 1

These tests lock in the correct lifecycle: the helper returns tools that can
be called repeatedly, back-to-back, on the same server.  ``create_supervisor_tools``
is generic over any MCP target, so a minimal in-process server with a couple
of toy tools exercises the reconnect contract without depending on any
A2A-specific server implementation.
"""

from __future__ import annotations

from langshark_bites.a2a_completion_notifier.supervisor_tools import create_supervisor_tools


def _fastmcp_cls():
    """Return the MCP server class, supporting mcp v1 and v2.

    mcp v1 ships ``mcp.server.fastmcp.FastMCP``; mcp v2 renamed it to
    ``mcp.server.mcpserver.MCPServer``.  Both expose the ``tool`` decorator
    used below.
    """
    try:
        from mcp.server import fastmcp

        return fastmcp.FastMCP
    except ImportError:
        from mcp.server import mcpserver

        return mcpserver.MCPServer


def _build_test_server():
    """Build a minimal in-process MCP server exposing two toy tools.

    ``create_supervisor_tools`` only cares about the reconnect-per-call
    contract, not any particular server's tool set, so ``ping``/``echo``
    stand in for a real deployment's tools.
    """
    mcp = _fastmcp_cls()(name="test-tools-server")

    @mcp.tool()
    async def ping() -> dict:
        return {"status": "ok"}

    @mcp.tool()
    async def echo(message: str) -> dict:
        return {"status": "ok", "message": message}

    return mcp


async def _tool_text(tool, **kwargs) -> str:
    """Invoke a LangChain tool (reconnectable) and return its text content."""
    result = await tool.ainvoke(kwargs or {})
    # LangChain MCP tools return (content_blocks, artifact); the text is in a list.
    blocks = result if isinstance(result, tuple) else (result,)
    # Flatten content blocks.
    parts = []
    for block in blocks[0]:
        if hasattr(block, "content"):
            parts.append(str(block.content))
        elif hasattr(block, "text"):
            parts.append(str(block.text))
        else:
            parts.append(str(block))
    return "\n".join(parts)


class TestCreateSupervisorTools:
    async def test_discovers_tools(self) -> None:
        tools = await create_supervisor_tools(_build_test_server())
        names = {t.name for t in tools}
        assert names == {"ping", "echo"}

    async def test_tools_reconnect_per_call_same_server(self) -> None:
        """The reentrancy crash regression: consecutive calls must NOT raise the
        fastmcp 'nesting counter' RuntimeError."""
        tools = await create_supervisor_tools(_build_test_server())
        ping = {t.name: t for t in tools}["ping"]

        # The crash happened on the SECOND call.  Call several times.
        for _ in range(3):
            text = await _tool_text(ping)
            assert '"status"' in text and '"ok"' in text

    async def test_tools_work_after_adapter_exited(self) -> None:
        tools = await create_supervisor_tools(_build_test_server())
        by_name = {t.name: t for t in tools}

        # Call one tool, then a different one back-to-back (interleaved calls).
        a = await _tool_text(by_name["ping"])
        b = await _tool_text(by_name["echo"], message="hi")
        c = await _tool_text(by_name["ping"])
        assert '"ok"' in a and '"ok"' in b and '"ok"' in c

    async def test_tool_with_args_reconnect(self) -> None:
        """The crash scenario from the field: a tool called twice back-to-back
        with the reconnect-per-call helper must not reproduce the nesting-
        counter crash."""
        tools = await create_supervisor_tools(_build_test_server())
        echo = {t.name: t for t in tools}["echo"]

        first = await _tool_text(echo, message="one")
        second = await _tool_text(echo, message="two")
        assert '"status"' in first and '"ok"' in first
        assert '"status"' in second and '"ok"' in second

    async def test_prewarm_supervisor_tools_warms_jsonschema(self) -> None:
        """The mcp SDK lazily imports jsonschema on the first call_tool; that
        import scans a directory (jsonschema_specifications' registry), which
        blockbuster rejects inside langgraph dev's event loop.  The graph
        module must call ``prewarm_supervisor_tools()`` at import time so the
        live call never scans in-loop."""
        import sys

        from langshark_bites.a2a_completion_notifier.supervisor_tools import (
            prewarm_supervisor_tools,
        )

        for mod in [m for m in list(sys.modules) if m.startswith("jsonschema")]:
            del sys.modules[mod]

        prewarm_supervisor_tools()

        assert "jsonschema.exceptions" in sys.modules
        assert "jsonschema.validators" in sys.modules
        assert "jsonschema_specifications" in sys.modules

    async def test_create_supervisor_tools_does_not_import_jsonschema(self) -> None:
        """Regression: ``create_supervisor_tools`` must NOT import jsonschema
        itself.  Under a running loop (any thread) blockbuster treats the scan
        as fatal, so the warm-up belongs to import-time only."""
        import sys

        for mod in [m for m in list(sys.modules) if m.startswith("jsonschema")]:
            del sys.modules[mod]
            sys.modules[mod] = None  # shadow: a re-import here would resurrect it

        await create_supervisor_tools(_build_test_server())

        # create_supervisor_tools must not have re-imported the subtree.
        assert all(sys.modules.get(m) is None for m in ("jsonschema", "jsonschema_specifications"))

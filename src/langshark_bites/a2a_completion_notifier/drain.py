"""Supervisor-side mailbox drain middleware (``before_model`` hook).

Why this exists
---------------
The receiver writes completion notices into the supervisor's LangGraph
``Store`` under ``("notifications", thread_id)``.  The supervisor graph must
*consume* that mailbox: at the start of each model call, inject everything
that has arrived into the model's context.  That is the "drain" half of the
mailbox + drain design (see design note §04).

``MailboxDrainMiddleware`` is an ``AgentMiddleware`` that:

1. resolves the current ``thread_id`` from ``runtime.execution_info``;
2. reads the mailbox namespace (``("notifications", thread_id)``);
3. drops the drained items from the Store (drain is idempotent: the
   completion text is already committed into the returned state update, so a
   superstep retry re-drains an already-empty mailbox instead of
   double-injecting);
4. returns a single ``HumanMessage`` carrying all pending notices, so the
   model sees the complete picture in one turn (coalescing is inherent).

Attach it to the **supervisor** graph's middleware stack.  It is a no-op
when there is no Store, no ``thread_id``, or nothing pending.

Dev-server parity
-----------------
``langgraph dev`` (the in-memory dev runtime) does not populate
``runtime.store`` on the middleware runtime, so this middleware **falls back
to a langgraph-sdk ``StoreClient``** built from ``A2A_SUPERVISOR_URL`` and
drains the same mailbox over HTTP.  The platform / Agent Server (Postgres)
runtime supplies ``runtime.store`` directly and is used unchanged.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

import structlog
from langchain.agents.middleware import AgentMiddleware

if TYPE_CHECKING:
    from langchain.agents.middleware.types import AgentState

MAILBOX_NAMESPACE_PREFIX = ("notifications",)

log = structlog.get_logger(__name__)

RenderNotice = Callable[[str, dict[str, Any]], str]

# A subset of the LangGraph ``Store`` contract that both the runtime handle
# and the langgraph-sdk ``StoreClient`` expose (sync and async closers overlap
# on the item-access surface we use; the SDK store uses ``search_items`` /
# ``delete_item``, the runtime handle uses ``asearch`` / ``adelete``).
type StoreLike = Any


def _http_store_from_env() -> Any:
    """Build a langgraph-sdk StoreClient from ``A2A_SUPERVISOR_URL``.

    ``langgraph dev`` does not populate ``runtime.store`` on the middleware
    runtime even when the graph is compiled with the server Store.  This
    helper builds an HTTP ``StoreClient`` (reading the SAME supervisor Agent
    Server mailbox) so the drain works there too.  Falls back to
    ``http://localhost:8000`` (the bite's ``ReceiverSettings.supervisor_url``
    default) when the env var is unset.
    """
    from langgraph_sdk import get_client

    url = os.environ.get("A2A_SUPERVISOR_URL", "http://localhost:8000")
    return get_client(url=url).store


class MailboxDrainError(Exception):
    """The mailbox search failed; pending completions could not be drained.

    Raised (not swallowed) so a Store outage is visible in the supervisor's
    error path instead of silently dropping every pending completion.
    """


def render_notice(task_id: str, value: dict[str, Any]) -> str:
    """Render one stored completion notice into a prompt-friendly block.

    Matches the ``[SUBAGENT COMPLETION NOTICE]`` convention so the model can
    recognise injected completions.

    Args:
        task_id: The A2A task id (Store key).
        value: The stored notice dict (``task_id``, ``state``, ``summary``...).

    Returns:
        A short prompt block for the supervisor's model.
    """
    summary = value.get("summary") or ""
    return (
        "[SUBAGENT COMPLETION NOTICE]\n"
        f"task_id: {task_id}\n"
        f"status: {value.get('state', 'completed')}\n"
        f"summary: {summary}"
    )


# Module-level default so __init__ can shadow the parameter name safely.
_DEFAULT_RENDER_NOTICE = render_notice


async def _read_mailbox(
    store: StoreLike,
    namespace: Sequence[str],
    max_notices: int,
) -> list[tuple[str, dict[str, Any]]]:
    """Read pending notices from a store handle (runtime or SDK-http).

    Args:
        store: The store handle.  Supports either the runtime ``Store``
            interface (``asearch``) or the langgraph-sdk ``StoreClient``
            (``search_items`` returning ``{"items": [...]}``).
        namespace: The mailbox namespace.
        max_notices: Cap on the number of items returned.

    Returns:
        ``[(key, value), ...]`` for each pending notice.

    Raises:
        MailboxDrainError: The mailbox search failed.
    """
    search = getattr(store, "asearch", None)
    if search is not None:
        items = await search(namespace, limit=max_notices)
        return [(item.key, item.value or {}) for item in items]

    search_items = getattr(store, "search_items", None)
    if search_items is None:
        raise MailboxDrainError("mailbox store handle exposes neither `asearch` nor `search_items`")
    resp = await search_items(namespace, limit=max_notices)
    items = resp.get("items", []) if isinstance(resp, dict) else list(resp or [])
    return [(item.get("key"), item.get("value") or {}) for item in items]


async def _delete_item(store: StoreLike, namespace: Sequence[str], task_id: str) -> None:
    """Consume one mailbox item from a store handle (runtime or SDK-http).

    Args:
        store: The store handle (runtime ``Store`` ``adelete`` or
            langgraph-sdk ``StoreClient`` ``delete_item``).
        namespace: The mailbox namespace.
        task_id: The Store key to delete.

    Raises:
        MailboxDrainError: The delete failed irrecoverably (still injected,
            logged by the caller).
    """
    delete = getattr(store, "adelete", None)
    if delete is not None:
        await delete(namespace, task_id)
        return
    await store.delete_item(namespace, task_id)


class MailboxDrainMiddleware(AgentMiddleware):
    """Drains pending A2A completions into each supervisor model call.

    Works with both the runtime ``Store`` handle (``langgraph up`` /
    platform Agent Server) and a langgraph-sdk HTTP ``StoreClient`` (e.g.
    ``langgraph dev`` where ``runtime.store`` is not populated): pass the
    ``store`` explicitly, or let it build one from ``A2A_SUPERVISOR_URL``
    (``ReceiverSettings.supervisor_url``) when the runtime handle is absent.

    Args:
        namespace_prefix: Store namespace prefix.  The thread id is appended
            to form the full mailbox namespace -- must match the receiver's
            mailbox configuration (default ``("notifications",)``).
        render_notice: Optional callable mapping ``(task_id, value)`` to a
            prompt string.  Defaults to :func:`render_notice`.
        max_notices: Cap on notices drained per model call.
        store: Optional store handle override.  When omitted, the runtime's
            ``runtime.store`` is used; if that is also absent, a langgraph-sdk
            ``StoreClient`` is built from ``A2A_SUPERVISOR_URL`` (falling back
            to ``http://localhost:8000``).
    """

    def __init__(
        self,
        *,
        namespace_prefix: tuple[str, ...] = MAILBOX_NAMESPACE_PREFIX,
        render_notice: RenderNotice | None = None,
        max_notices: int = 50,
        store: StoreLike | None = None,
    ) -> None:
        """Build the drain middleware (see class Args for each option)."""
        self._namespace_prefix = namespace_prefix
        self._render = render_notice if render_notice is not None else _DEFAULT_RENDER_NOTICE
        self._max_notices = max_notices
        self._store = store

    async def abefore_model(
        self,
        state: AgentState[Any],  # noqa: ARG002 - fixed middleware hook signature
        runtime: Any,
    ) -> dict[str, Any] | None:
        """Notify (no-op) if nothing is pending, else return a state update.

        Raises:
            MailboxDrainError: The mailbox ``asearch`` failed.  A Store
                outage must not silently drop pending completions.
        """
        info = getattr(runtime, "execution_info", None)
        if info is None:
            return None
        thread_id = getattr(info, "thread_id", None)
        if not thread_id:
            return None

        store = self._store or getattr(runtime, "store", None)

        namespace = (*self._namespace_prefix, thread_id)
        try:
            if store is None:
                # `langgraph dev` does not populate `runtime.store`; drain the
                # same mailbox over the LangGraph SDK HTTP store instead.
                store = _http_store_from_env()
            pending = await _read_mailbox(store, namespace, self._max_notices)
        except Exception as exc:
            log.error(
                "a2a_drain_search_failed",
                thread_id=thread_id,
                exc_info=exc,
            )
            raise MailboxDrainError(f"mailbox search failed for thread {thread_id!r}") from exc

        if not pending:
            return None

        # Consume from the mailbox before injecting; a failed superstep retry
        # therefore re-drains an empty mailbox (idempotent).
        notices: list[str] = []
        for task_id, value in pending:
            notices.append(self._render(task_id, value))
            try:
                await _delete_item(store, namespace, task_id)
            except Exception as exc:
                # The notice is already rendered into the returned update, so
                # we still inject it; a failed superstep retry re-drains an
                # unconsumed mailbox (documented idempotency cost).  Log at
                # error level -- deletion is best-effort cleanup, but a silent
                # failure would hide unbounded mailbox growth.
                log.error(
                    "a2a_drain_delete_failed",
                    thread_id=thread_id,
                    task_id=task_id,
                    exc_info=exc,
                )

        from langchain_core.messages import HumanMessage

        content = "\n\n".join(notices)
        log.info("a2a_drained", thread_id=thread_id, count=len(notices))
        return {"messages": [HumanMessage(content=content)]}

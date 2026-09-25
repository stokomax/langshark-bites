"""Mailbox delivery: write completions to the supervisor's Store + wake it.

Why this exists
---------------
This is the supervisor-side *delivery* half.  The receiver has already
verified and deduplicated a notification and learned the parent
``CallbackToken``.  ``NotificationMailbox`` then:

1. Writes the completion into the LangGraph ``Store`` under
   ``("notifications", thread_id)`` keyed by ``task_id`` (write first);
2. Checks whether a run is pending or running on that thread, and if not,
   creates a trigger run carrying :data:`DEFAULT_WAKE_INPUT` -- a minimal
   user message -- so the supervisor wakes and its ``before_model``
   middleware drains the mailbox.  A run with *no* input is a silent no-op
   on an idle (non-interrupted) thread in some Agent Server versions, so
   the wake must always carry real content (see :data:`DEFAULT_WAKE_INPUT`).

Write-first/check-second ordering makes the check-then-create race resolve
to a harmless empty extra run instead of a lost notification.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from langshark_bites.a2a_completion_notifier.tokens import CallbackToken

log = structlog.get_logger(__name__)

NOTIFICATION_NAMESPACE_PREFIX = "notifications"

# The wake-run input used when the caller does not supply one.  A real user
# message (not ``None``) is required: on an idle, completed thread a run with
# ``input=None`` is a silent no-op in langgraph-api (``run_exec_ms`` of ~tens
# of milliseconds, no node executes), so the ``before_model`` drain never
# fires and the supervisor never wakes.  The drain middleware supplies the
# completion notice itself; this message only makes the graph actually run.
DEFAULT_WAKE_INPUT: dict[str, Any] = {
    "messages": [
        {
            "role": "user",
            "content": (
                "[completion notifier] wake: check for pending subagent "
                "completion notices and respond."
            ),
        }
    ]
}


class MailboxWriteError(Exception):
    """The Store write failed irrecoverably."""


class MailboxWakeError(Exception):
    """The wake-up trigger run failed to be created.

    Distinct from :class:`MailboxWriteError`: the mailbox may have been
    written successfully but the supervisor never woke, so the completion
    sits undelivered.  Callers (e.g. the receiver's post-ACK ``_forward``)
    must surface this, not swallow it.
    """


class NotificationMailbox:
    """Writes completion notifications to the supervisor's Store.

    Args:
        lg: A LangGraph SDK client for the supervisor Agent Server
            (``langgraph_sdk.get_client()``) exposing ``store`` and
            ``runs``.
        store_namespace_prefix: Leading tuple of a Store namespace.
            The mailbox appends ``thread_id`` to form the full namespace.
        wake_input: The trigger-run input.  Defaults to
            :data:`DEFAULT_WAKE_INPUT` (a minimal nudge message) so the wake
            run actually executes on an idle thread instead of silently
            no-op'ing.  Pass ``None`` for a truly input-less run (original
            behavior, e.g. interrupt/resume flows); the drain middleware
            supplies the content either way.
        wake_multitask_strategy: Strategy when a run is already queued.
            ``"enqueue"`` is the only option that does not drop work.
    """

    def __init__(
        self,
        lg: Any,
        *,
        store_namespace_prefix: tuple[str, ...] = (NOTIFICATION_NAMESPACE_PREFIX,),
        wake_input: Any | None = DEFAULT_WAKE_INPUT,
        wake_multitask_strategy: str = "enqueue",
    ) -> None:
        """Build a mailbox over the supervisor client ``lg`` (see class Args)."""
        self._lg = lg
        self._namespace_prefix = store_namespace_prefix
        self._wake_input = wake_input
        self._wake_multitask_strategy = wake_multitask_strategy

    async def deliver(
        self,
        parent: CallbackToken,
        task_id: str,
        state: str,
        summary: str | None = None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Write one completion notice into the thread's mailbox.

        Args:
            parent: Unsealed callback token giving the target thread.
            task_id: A2A task id; doubles as the Store key, so a redelivery
                overwrites instead of appending.
            state: Terminal state (``completed`` / ``failed`` / ...).
            summary: Optional short result summary for the drain.
            extra: Optional extra fields merged into the stored value.
                The receiver passes the sender ``iss`` here so the full-result
                primitive can route back to the deployment.

        Raises:
            MailboxWriteError: If the Store write fails.
        """
        value: dict[str, Any] = {
            "task_id": task_id,
            "state": state,
            "dispatch_id": parent.dispatch_id,
        }
        if summary is not None:
            value["summary"] = summary
        if extra:
            value.update(extra)

        namespace = (*self._namespace_prefix, parent.thread_id)
        try:
            # `namespace` is positional-only on the langgraph-sdk StoreClient
            # (it accepts no keyword forms), so don't pass it by keyword.
            await self._lg.store.put_item(
                namespace,
                task_id,
                value,
            )
            log.info(
                "a2a_mailbox_write",
                thread_id=parent.thread_id,
                task_id=task_id,
                state=state,
            )
        except Exception as exc:
            raise MailboxWriteError(f"store.put_item failed for task {task_id!r}") from exc

    async def trigger_if_idle(self, parent: CallbackToken) -> bool:
        """Create a wake-up run only if no run is pending/running.

        Covers the queue-sweep middle state (``pending``/``running``) so a
        swept run cycling between states is not double-woken.

        Args:
            parent: Unsealed callback token giving the target thread.

        Returns:
            True if a wake-up run was created; False if a run was already
            pending/running (no wake needed) or the liveness check could not
            determine the run state.

        Raises:
            MailboxWakeError: The wake-up run create call failed.  The
                mailbox may already hold the completion, but the supervisor
                never woke -- callers must surface this rather than swallow
                it.

        Notes:
            The wake run always carries ``self._wake_input`` (default
            :data:`DEFAULT_WAKE_INPUT`).  Construct with ``wake_input=None``
            to emit a resume-style input-less run instead.
        """
        try:
            active = await self._lg.runs.list(
                parent.thread_id,
                status=["pending", "running"],
                limit=1,
            )
        except Exception as exc:
            log.warning("a2a_liveness_check_failed", exc_info=exc)
            return False

        if active:
            return False

        try:
            create_kwargs: dict[str, Any] = {
                "thread_id": parent.thread_id,
                "assistant_id": parent.assistant_id,
                "multitask_strategy": self._wake_multitask_strategy,
            }
            if self._wake_input is not None:
                create_kwargs["input"] = self._wake_input
            await self._lg.runs.create(**create_kwargs)
            log.info("a2a_wake_triggered", thread_id=parent.thread_id)
            return True
        except Exception as exc:
            log.error(
                "a2a_wake_create_failed",
                thread_id=parent.thread_id,
                exc_info=exc,
            )
            raise MailboxWakeError(
                f"wake-up run create failed for thread {parent.thread_id!r}"
            ) from exc

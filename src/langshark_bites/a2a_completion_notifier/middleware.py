"""A2A push-notification emitter middleware for subagent graphs.

Why this exists
---------------
LangChain does not implement A2A push notifications: the Agent Server
supports ``message/send``, ``message/stream``, ``tasks/get``,
``tasks/cancel`` on the inbound side, but ``*TaskPushNotificationConfig``
and ``SubscribeToTask`` return ``-32601`` (not implemented).  When the
supervisor dispatches work to a subagent on a separate server, nothing on
the subagent server emits a completion webhook.  This middleware provides
that missing server-side push behaviour on the *subagent* graph's stack.

It is middleware, not a tool, by design:

- ``aafter_agent`` fires once per invocation, unconditionally, after the
  agent completes -- exactly when a completion webhook must go out.
- ``awrap_model_call`` catches exceptions so a *failed* run also notifies,
  then re-raises.  A tool call could do neither (it depends on the model
  choosing to call it, and runs *inside* the turn).

How the dispatch config gets here
---------------------------------
The supervisor passes the ``PushNotificationConfig`` (webhook ``url`` +
opaque callback ``token``) at dispatch time via ``config.configurable``.
Because middleware hooks do not receive the ``RunnableConfig``, the graph
is built by a **dynamic graph factory** that reads it and constructs this
middleware -- the same pattern the reference `async-deep-agents`
`completion_notifier` uses for ``parent_thread_id``.  See
``build_a2a_notifier_from_config``.

The emitter signs the outgoing JWT with the subagent deployment's RS256
key (``A2ASigner``) and POSTs with retry via ``PushClient``.  **Delivery**
failures (the webhook itself is down/errors) are logged and swallowed -- a
failed webhook must never take down the subagent's own run.  A failure to
*construct or sign* the notification is different: it raises
:class:`PushEmissionError` so the missing push is not silently lost (on the
``awrap_model_call`` failed-run path, the model's original error is still
re-raised).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from langchain.agents.middleware import AgentMiddleware

from langshark_bites.a2a_completion_notifier.payload import build_notification

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from langchain.agents.middleware.types import AgentState

    from langshark_bites.a2a_completion_notifier.push_client import PushClient
    from langshark_bites.a2a_completion_notifier.signer import A2ASigner

log = structlog.get_logger(__name__)


class PushNotificationConfigError(ValueError):
    """The push config in ``config.configurable`` is missing or invalid."""


class PushEmissionError(RuntimeError):
    """The completion notification could not be built or signed.

    Distinct from delivery failure: ``PushClient.send`` already reports its
    own retry/failure surface.  This surfaces a failure to *construct* the
    notification (task id missing, signer failed) instead of silently
    swallowing it -- a non-notified completion is a correctness gap, not a
    best-effort no-op.
    """


class PushNotificationConfig:
    """A2A ``TaskPushNotificationConfig`` equivalent for one dispatch.

    Mirrors the A2A spec field names so a supervisor-side builder can
    produce this from a dict without an import cycle.

    Attributes:
        url: Webhook URL the subagent server must POST the notification to
            (the supervisor's receiver ``/a2a/notifications``).
        token: Opaque callback token, echoed back uninterpreted in the
            notification ``metadata``.
        authentication: Optional auth mapping
            (e.g. ``{"schemes": ["Bearer"]}``).
    """

    __slots__ = ("authentication", "context_id", "mode", "token", "url")

    def __init__(
        self,
        url: str,
        token: str,
        authentication: Mapping[str, Any] | None = None,
        mode: str = "dev",
        context_id: str | None = None,
    ) -> None:
        """Build a push config (see class Attributes); validates ``url``/``token``."""
        if not url:
            raise PushNotificationConfigError("push config 'url' is required")
        if token is None:
            raise PushNotificationConfigError("push config 'token' is required")
        self.url = url
        self.token = token
        self.authentication = dict(authentication) if authentication else {}
        self.mode = mode
        self.context_id = context_id

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PushNotificationConfig:
        """Build from a raw mapping (as placed under ``a2a_push_config``)."""
        ctx = data.get("context_id") or data.get("contextId")
        return cls(
            url=str(data.get("url") or ""),
            token=str(data.get("token") or ""),
            authentication=data.get("authentication"),
            mode=str(data.get("mode") or "dev"),
            context_id=str(ctx) if ctx else None,
        )


def extract_push_config(
    config: Mapping[str, Any] | None,
) -> PushNotificationConfig | None:
    """Read ``config.configurable["a2a_push_config"]``, if present.

    Returns:
        A parsed ``PushNotificationConfig``, or None when the runnable
        config has no ``a2a_push_config`` (the run was not dispatched as a
        subagent task, so there is nothing to notify).
    """
    if not config:
        return None
    raw = (config.get("configurable") or {}).get("a2a_push_config")
    if not raw:
        return None
    return PushNotificationConfig.from_dict(raw)


def _default_task_id(runtime: Any) -> str | None:
    """Best-effort A2A task id from the run's ``execution_info``.

    Prefers ``thread_id`` over ``run_id``: the A2A task id must be
    addressable by the supervisor's result fetch (``GET
    /threads/{task_id}/state``), and the async-agent framework uses the
    thread id as the task id.  A per-run ``run_id`` is not fetchable.
    """
    info = getattr(runtime, "execution_info", None)
    if info is None:
        return None
    task_id = info.thread_id or info.run_id
    return task_id if isinstance(task_id, str) else None


class A2APushNotifierMiddleware(AgentMiddleware):
    """Emits an A2A completion notification at terminal run states.

    Attach to the **subagent** graph's middleware stack.  Add it last so
    every other middleware has finished before the notification fires.

    Args:
        push_config: Webhook target + opaque callback token for this run.
        signer: RS256 signer for the subagent deployment.
        push_client: HTTP client that POSTs the signed notification.
        task_id_resolver: Optional callable mapping the hook ``runtime`` to
            the A2A task id.  Defaults to ``runtime.execution_info.thread_id``
            (then ``run_id``) so the id is fetchable by the supervisor's
            full-result primitive (the async-agent "thread id == task id"
            convention).
    """

    def __init__(
        self,
        push_config: PushNotificationConfig,
        signer: A2ASigner | None = None,
        push_client: PushClient | None = None,
        task_id_resolver: Callable[[Any], str | None] | None = None,
    ) -> None:
        """Build the emitter middleware; strict mode requires a ``signer``."""
        if push_config.mode == "strict" and signer is None:
            raise PushNotificationConfigError(
                "strict-mode dispatch requires an emitter signing key "
                "(pass signer= to build_a2a_notifier_from_config)"
            )
        self._push_config = push_config
        self._signer = signer
        self._push_client = push_client
        self._task_id_resolver = task_id_resolver or _default_task_id
        self._dispatched = False
        self._task_id: str | None = None

    async def abefore_agent(
        self,
        state: AgentState[Any],  # noqa: ARG002 - fixed middleware hook signature
        runtime: Any,
    ) -> dict[str, Any] | None:
        """Resolve and cache the A2A task id for this run.

        Fires before the first model call, so ``awrap_model_call`` can emit a
        failed notification even though it does not receive a ``runtime``.
        """
        self._task_id = self._task_id_resolver(runtime)
        return None

    async def aafter_agent(self, state: AgentState[Any], runtime: Any) -> dict[str, Any] | None:
        """Fire the 'completed' notification after the agent finishes."""
        if self._task_id is None:
            self._task_id = self._task_id_resolver(runtime)
        await self._notify("completed", state)
        return None

    async def awrap_model_call(self, request: Any, handler: Callable[..., Any]) -> Any:
        """Fire a 'failed' notification on exception, then re-raise.

        Uses the task id resolved by ``abefore_agent``.  If the *notification
        itself* fails to build, that ``PushEmissionError`` is logged but must
        not mask the model exception being propagated.
        """
        try:
            return await handler(request)
        except Exception as exc:
            try:
                await self._notify("failed", error=str(exc))
            except PushEmissionError as notify_exc:
                log.error(
                    "a2a_emit_failed_notify_errored",
                    error=str(exc),
                    notify_error=str(notify_exc),
                )
            raise

    async def _notify(
        self,
        state: str,
        agent_state: Mapping[str, Any] | None = None,
        *,
        error: str | None = None,
    ) -> None:
        """Send at most one notification per run instance."""
        if self._dispatched:
            return
        self._dispatched = True

        task_id = self._task_id
        if not task_id:
            log.error(
                "a2a_emitter_no_task_id",
                state=state,
                push_url=self._push_config.url,
                reason="could not resolve task id from execution_info",
            )
            raise PushEmissionError(
                "cannot emit A2A completion notification: could not resolve "
                "the task id from execution_info"
            )

        summary = _last_message_summary(agent_state) if agent_state else None
        try:
            payload = build_notification(
                task_id=task_id,
                state=state,
                token=self._push_config.token,
                context_id=self._push_config.context_id,
                summary=summary or error,
            )
            bearer = self._signer.sign(task_id) if self._signer is not None else None
        except Exception as exc:
            log.error("a2a_emit_build_failed", state=state, exc_info=exc)
            raise PushEmissionError(
                f"failed to build/sign A2A completion notification for task {task_id!r}"
            ) from exc

        client = self._push_client
        if client is None:
            raise PushEmissionError(
                f"cannot emit A2A completion notification for task {task_id!r}: "
                "no push_client configured"
            )
        ok = await client.send(self._push_config.url, bearer=bearer, payload=payload)
        log.info(
            "a2a_emitted",
            state=state,
            task_id=task_id,
            delivered=ok,
            push_url=self._push_config.url,
            signed=bearer is not None,
        )


def build_a2a_notifier_from_config(
    config: Mapping[str, Any] | None,
    signer: A2ASigner | None = None,
    push_client: PushClient | None = None,
    task_id_resolver: Callable[[Any], str | None] | None = None,
) -> A2APushNotifierMiddleware:
    """Factory for dynamic subagent graphs: read config, build middleware.

    Use inside a graph factory so the middleware is constructed per run
    with the dispatch's webhook config::

        def make_graph(config):
            notifier = build_a2a_notifier_from_config(
                config, signer=signer, push_client=client,
            )
            agent = create_agent(model=..., tools=..., middleware=[notifier])
            return agent

    Args:
        config: The runnable ``config`` (containing ``configurable``).
        signer: Optional deployment signer.  When omitted, notifications are
            sent unsigned -- accepted by ``dev`` and (for unsigned senders)
            ``verify`` receivers, rejected by ``strict`` receivers.
        push_client: Delivery client.
        task_id_resolver: Override for the A2A task id resolver.  The default
            resolves to ``execution_info.thread_id`` (then ``run_id``) so the
            id is fetchable by the supervisor's full-result primitive.

    Raises:
        PushNotificationConfigError: If no ``a2a_push_config`` is present, or
            a ``strict``-mode dispatch arrives without a signer.
    """
    push_config = extract_push_config(config)
    if push_config is None:
        raise PushNotificationConfigError(
            "config.configurable['a2a_push_config'] is required for a "
            "dispatched subagent; is this graph being run as an async subagent?"
        )
    return A2APushNotifierMiddleware(
        push_config=push_config,
        signer=signer,
        push_client=push_client,
        task_id_resolver=task_id_resolver,
    )


def _last_message_summary(state: Mapping[str, Any]) -> str | None:
    """Pull a short summary from the final message in agent state."""
    messages = state.get("messages") or []
    if not messages:
        return None
    last = messages[-1]
    content = getattr(last, "content", None)
    if content is None and isinstance(last, dict):
        content = last.get("content")
    if not isinstance(content, str):
        content = str(content) if content is not None else ""
    return content[:500]

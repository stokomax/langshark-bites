"""Supervisor-side A2A notification receiver (FastAPI webhook endpoint).

Why this exists
---------------
A LangGraph Agent Server has no generic inbound webhook endpoint -- the
A2A push notification has nowhere to land.  This receiver is the supervisor-
side component that terminates ``POST /a2a/notifications``, authenticates
the sender, deduplicates, unseals the callback token, and delivers the
completion into the supervisor via the mailbox (``NotificationMailbox``).

It also exposes ``POST /a2a/result`` -- the sister primitive to the async-
agent framework's poll-based ``check_async_task``.  Because the notifier
already proved the task is terminal, the supervisor can fetch the full
result of a completed task with a single on-demand call (no status gate):
the receiver forwards the task id to the configured subagent deployment and
returns its thread state.

Deployment rule (from the design notes): the receiver lives **next to the
supervisor**, not the subagents.  It holds the supervisor's API key and the
``CALLBACK_TOKEN_SECRET``; neither ever crosses to the subagent side.

The handler logic is also exported as framework-neutral functions
(``process_notification`` / ``process_task_result`` / ``process_health``)
so alternative hosts can reuse the same webhook logic without depending on
the FastAPI app itself.

Notification request lifecycle (in this exact order):

1. Unseal the callback token -- the routing invariant, enforced in every
   ``A2A_MODE`` (``dev`` / ``verify`` / ``strict``).
2. Sender verification, dispatched by mode: ``dev`` accepts signed and
   unsigned unverified; ``verify`` verifies signed notifications (failing
   closed) while accepting unsigned ones; ``strict`` rejects unsigned and
   always verifies.
3. Claim the dedup key exactly once (JWT ``jti`` when signed, else the
   payload ``id``).
4. Accept only terminal states (``completed`` / ``failed`` / ...).
5. ``taskId`` inside the JWT must match the payload ``id`` (signed only).
6. ACK with ``202`` (``BackgroundTasks``), then mailbox.write + wake.
7. The sender ``iss`` is persisted with the mailbox notice for later
   full-result routing.

Usage
-----
    from langshark_bites.a2a_completion_notifier.receiver import create_receiver_app
    from langshark_bites.a2a_completion_notifier.settings import ReceiverSettings

    app = create_receiver_app(settings=ReceiverSettings.from_env())
    # uvicorn main:app --port 8001
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException

from langshark_bites.a2a_completion_notifier.auth import (
    JWKSClient,
    SenderAuthError,
    verify_sender_jwt,
)
from langshark_bites.a2a_completion_notifier.idempotency import (
    InMemoryJtiStore,
    JtiStore,
    RedisJtiStore,
)
from langshark_bites.a2a_completion_notifier.mailbox import (
    DEFAULT_WAKE_INPUT,
    MailboxWakeError,
    MailboxWriteError,
    NotificationMailbox,
)
from langshark_bites.a2a_completion_notifier.payload import (
    NotificationPayloadError,
    extract_callback_token,
    extract_summary,
    extract_task_id,
    extract_terminal_state,
    normalize_notification_payload,
)
from langshark_bites.a2a_completion_notifier.result_fetch import (
    ResultFetchSettings,
    TaskResultError,
    fetch_task_result,
)
from langshark_bites.a2a_completion_notifier.settings import A2AVerifyMode
from langshark_bites.a2a_completion_notifier.tokens import (
    CallbackTokenError,
    unseal_callback_token,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langshark_bites.a2a_completion_notifier.settings import ReceiverSettings

log = structlog.get_logger(__name__)


class ReceiverError(Exception):
    """A hard receiver rejection that maps to an HTTP error status.

    Framework-neutral: the FastAPI wrapper translates it to
    ``HTTPException``.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        """Reject with HTTP ``status_code`` and the message ``detail``."""
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def process_health() -> dict[str, str]:
    """Shared liveness body for the FastAPI route."""
    return {"status": "ok"}


async def _resolve_sender_claims(
    *,
    settings: ReceiverSettings,
    jwks_client: JWKSClient,
    authorization: str | None,
    signed: bool,
) -> dict[str, Any] | None:
    """Mode-dispatched sender JWT verification; returns claims or None."""
    if settings.mode is A2AVerifyMode.STRICT:
        if not signed:
            raise ReceiverError(status_code=401, detail="sender JWT required in strict mode")
        if not settings.subagent_jwks_url or not settings.subagent_issuer:
            log.error("a2a_strict_missing_sender_auth_config")
            raise ReceiverError(status_code=502, detail="receiver sender-auth config missing")
        assert authorization is not None  # signed checked above
        return await _verify_sender(settings, jwks_client, authorization)

    if settings.mode is A2AVerifyMode.VERIFY:
        if signed and settings.subagent_jwks_url and settings.subagent_issuer:
            assert authorization is not None  # signed checked above
            return await _verify_sender(settings, jwks_client, authorization)
        if signed:
            log.warning(
                "a2a_verify_signed_unverifiable_accepted",
                reason="sender-auth config not populated",
            )
        else:
            log.info("a2a_verify_unsigned_accepted")
        return None

    if signed:
        log.info("a2a_dev_ignores_sender_jwt")
    else:
        log.info("a2a_dev_unsigned_accepted")
    return None


def _dedup_key_for(claims: dict[str, Any] | None, payload: dict[str, Any]) -> str:
    """JWT ``jti`` when signed, else ``id:<task id>``."""
    if claims is None:
        return f"id:{extract_task_id(payload)}"
    raw_jti = claims.get("jti")
    if not isinstance(raw_jti, str) or not raw_jti:
        raise ReceiverError(status_code=400, detail="sender JWT missing jti")
    return raw_jti


def _sender_iss_for(claims: dict[str, Any] | None, settings: ReceiverSettings) -> str:
    """Prefer the verified ``iss`` claim; fall back to configured issuer."""
    if claims is not None:
        raw_iss = claims.get("iss")
        if isinstance(raw_iss, str):
            return raw_iss
    return settings.subagent_issuer or ""


async def process_notification(
    payload: dict[str, Any],
    *,
    settings: ReceiverSettings,
    jti_store: JtiStore,
    jwks_client: JWKSClient,
    mailbox: NotificationMailbox,
    authorization: str | None = None,
) -> tuple[dict[str, Any], Callable[[], Awaitable[None]] | None]:
    """Validate one A2A notification and stage its mailbox delivery.

    This is the framework-neutral core of ``POST /a2a/notifications``, used
    by the standalone FastAPI receiver.  Full lifecycle: unseal the
    callback token (routing invariant, all modes) -> mode-dispatched sender
    verification -> claim the dedup key exactly once -> accept only terminal
    states -> ``taskId`` cross-check (signed only).

    Returns:
        ``(body, forward)``: the response body and an optional async
        callable that must run *after* the ``202`` ACK has left the process
        (writes the mailbox, then wakes an idle supervisor).  ``forward`` is
        None for duplicate/ignored outcomes.

    Raises:
        ReceiverError: A hard rejection already mapped to an HTTP status
            (400 for routing/taskId defects, 401/502 for sender-auth).
    """
    # 0. A2A Task shape (sparse profile); legacy bite dicts upgraded here.
    try:
        payload = normalize_notification_payload(payload)
    except NotificationPayloadError as exc:
        raise ReceiverError(status_code=400, detail=str(exc)) from exc

    # 1. Routing invariant -- the callback token is checked in every mode.
    try:
        token = extract_callback_token(payload)
        parent = unseal_callback_token(token, settings.callback_token_secret)
    except (ValueError, CallbackTokenError) as exc:
        raise ReceiverError(status_code=400, detail=str(exc)) from exc

    # 2. Mode-dispatched sender verification.
    signed = bool(authorization and authorization.startswith("Bearer "))
    claims = await _resolve_sender_claims(
        settings=settings,
        jwks_client=jwks_client,
        authorization=authorization,
        signed=signed,
    )

    # 3. Dedup: signed notifications claim the JWT ``jti``; unsigned ones
    #    fall back to the Task id.
    try:
        dedup_key = _dedup_key_for(claims, payload)
    except NotificationPayloadError as exc:
        raise ReceiverError(status_code=400, detail=str(exc)) from exc
    if not await jti_store.claim(dedup_key):
        # Redelivery after an ACK was lost -- dedup at the boundary.
        return {"status": "duplicate"}, None

    state = extract_terminal_state(payload)
    if state is None:
        return {
            "status": "ignored",
            "state": str((payload.get("status") or {}).get("state")),
        }, None

    task_id = extract_task_id(payload)

    # 4. The ``taskId`` claim can only be cross-checked when signed.
    if claims is not None and claims.get("taskId") != task_id:
        raise ReceiverError(status_code=400, detail="taskId mismatch")

    forward = functools.partial(
        _forward,
        mailbox,
        parent,
        task_id,
        state,
        extract_summary(payload),
        _sender_iss_for(claims, settings),
    )
    return {"status": "accepted"}, forward


async def process_task_result(
    body: dict[str, Any],
    *,
    result_settings: ResultFetchSettings,
    result_http: httpx.AsyncClient,
) -> dict[str, Any]:
    """Fetch a completed subagent task's full result (sister primitive).

    Framework-neutral core of ``POST /a2a/result``, used by the FastAPI
    receiver.

    Returns:
        ``{"task_id": ..., "status": "ok", "result": {...state...}}``.

    Raises:
        ReceiverError: 400 for a missing ``task_id``, 502 when the result
            fetch fails.
    """
    task_id = str(body.get("task_id") or "").strip()
    if not task_id:
        raise ReceiverError(status_code=400, detail="missing task_id")
    try:
        state = await fetch_task_result(
            task_id,
            settings=result_settings,
            http=result_http,
        )
    except TaskResultError as exc:
        raise ReceiverError(status_code=502, detail=str(exc)) from exc
    return {"task_id": task_id, "status": "ok", "result": state}


def _default_lg(settings: ReceiverSettings) -> Any:
    """Build the LangGraph SDK client for the supervisor Agent Server."""
    from langgraph_sdk import get_client

    return get_client(
        url=settings.supervisor_url,
        api_key=settings.supervisor_api_key or None,
    )


def _default_jti_store(settings: ReceiverSettings) -> JtiStore:
    """Redis-backed ``jti`` dedup when ``redis_url`` is configured.

    Cross-replica atomic dedup requires a shared ``SET NX EX``; without a
    Redis URL the single-process in-memory store is the fallback (safe for
    one replica only).
    """
    if not settings.redis_url:
        return InMemoryJtiStore(ttl_seconds=settings.jti_ttl_seconds)
    try:
        import redis.asyncio

        return RedisJtiStore(
            redis.asyncio.from_url(settings.redis_url),
            ttl_seconds=settings.jti_ttl_seconds,
        )
    except Exception as exc:
        log.warning(
            "a2a_redis_wiring_failed",
            redis_url=settings.redis_url,
            error=str(exc),
        )
        return InMemoryJtiStore(ttl_seconds=settings.jti_ttl_seconds)


def _wire_receiver_deps(
    *,
    settings: ReceiverSettings,
    lg: Any | None,
    jti_store: JtiStore | None,
    jwks_client: JWKSClient | None,
    mailbox: NotificationMailbox | None,
    wake_input: Any | None,
    result_settings: ResultFetchSettings | None,
    result_http: httpx.AsyncClient | None,
) -> tuple[
    Any,
    JtiStore,
    JWKSClient,
    NotificationMailbox,
    ResultFetchSettings,
    httpx.AsyncClient,
]:
    """Fill in omitted receiver dependencies with production defaults."""
    if lg is None:
        lg = _default_lg(settings)
    if jti_store is None:
        jti_store = _default_jti_store(settings)
    if jwks_client is None:
        jwks_client = JWKSClient(settings.subagent_jwks_url)
    if mailbox is None:
        mailbox = NotificationMailbox(lg, wake_input=wake_input)
    if result_settings is None:
        result_settings = ResultFetchSettings(
            subagent_url=settings.subagent_url,
            subagent_api_key=settings.subagent_api_key,
        )
    if result_http is None:
        result_http = httpx.AsyncClient()
    return lg, jti_store, jwks_client, mailbox, result_settings, result_http


def create_receiver_app(
    *,
    settings: ReceiverSettings,
    lg: Any | None = None,
    jti_store: JtiStore | None = None,
    jwks_client: JWKSClient | None = None,
    mailbox: NotificationMailbox | None = None,
    wake_input: Any | None = DEFAULT_WAKE_INPUT,
    result_settings: ResultFetchSettings | None = None,
    result_http: httpx.AsyncClient | None = None,
) -> FastAPI:
    """Build the receiver FastAPI application.

    Every dependency can be injected for tests; defaults are wired lazily
    when omitted:

    - ``lg`` -- ``langgraph_sdk.get_client(url, api_key)`` for the
      supervisor Agent Server.
    - ``jti_store`` -- ``InMemoryJtiStore``.  **In production pass a
      ``RedisJtiStore``** so cross-replica dedup is atomic.
    - ``jwks_client`` -- fetches from ``settings.subagent_jwks_url``.
    - ``mailbox`` -- ``NotificationMailbox`` over ``lg``.
    - ``result_settings`` -- defaults to ``ResultFetchSettings`` from
      ``settings.subagent_url`` / ``subagent_api_key``; used by the
      ``POST /a2a/result`` sister primitive.
    - ``result_http`` -- the outbound HTTP client for full-result fetches.

    Args:
        settings: Supervisor-side receiver configuration.
        lg: LangGraph SDK client (supervisor).  Injected for tests.
        jti_store: Single-use ``jti`` claim store.
        jwks_client: Source of the subagent deployment's public keys.
        mailbox: Delivery sink for accepted notifications.
        wake_input: Input for the wake-up run created after a mailbox write.
            Defaults to ``NotificationMailbox.DEFAULT_WAKE_INPUT``; pass
            ``None`` for a resume-style input-less run.
        result_settings: Fetch configuration for the subagent deployment.
        result_http: HTTP client used to fetch completed task results.

    Returns:
        A configured FastAPI application.
    """
    settings.ensure_valid()
    (
        lg,
        jti_store,
        jwks_client,
        mailbox,
        result_settings,
        result_http,
    ) = _wire_receiver_deps(
        settings=settings,
        lg=lg,
        jti_store=jti_store,
        jwks_client=jwks_client,
        mailbox=mailbox,
        wake_input=wake_input,
        result_settings=result_settings,
        result_http=result_http,
    )

    app = FastAPI(title="a2a-completion-receiver")

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe for orchestrators and the MCP diagnostic tool."""
        return await process_health()

    @app.post("/a2a/notifications", status_code=202)
    async def receive_notification(
        payload: dict[str, Any],
        background: BackgroundTasks,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Terminate an A2A push notification from a subagent server.

        Thin FastAPI adapter over :func:`process_notification`: run the
        shared validation, translate :class:`ReceiverError` into
        ``HTTPException``, and hand the post-ACK mailbox delivery to
        ``BackgroundTasks``.

        Raises:
            HTTPException 400: Bad callback token or ``taskId`` mismatch.
            HTTPException 401/502: Sender verification failed.
        """
        body, forward = await _dispatch_notification(
            payload,
            settings=settings,
            jti_store=jti_store,
            jwks_client=jwks_client,
            mailbox=mailbox,
            authorization=authorization,
        )
        if forward is not None:
            background.add_task(forward)
        return body

    @app.post("/a2a/result")
    async def get_task_result(body: dict[str, Any]) -> dict[str, Any]:
        """Fetch the full result of a completed subagent task.

        Sister primitive to the async-agent framework's poll-based
        ``check_async_task``: because the completion notifier already proved
        the task is terminal, this is a single on-demand fetch (no status
        round-trip).  ``task_id`` is resolved by the emitter to the subagent's
        ``thread_id``, so the fetch reads the Agent Protocol thread state
        ``GET /threads/{task_id}/state`` on the configured subagent.

        Returns:
            ``{"task_id": ..., "status": "ok", "result": {...state...}}``.

        Raises:
            HTTPException 400: Missing ``task_id``.
            HTTPException 502: The result fetch failed (unconfigured URL,
                unreachable subagent, or non-2xx from the subagent).
        """
        return await _dispatch_task_result(
            body,
            result_settings=result_settings,
            result_http=result_http,
        )

    return app


async def _dispatch_notification(
    payload: dict[str, Any],
    *,
    settings: ReceiverSettings,
    jti_store: JtiStore,
    jwks_client: JWKSClient,
    mailbox: NotificationMailbox,
    authorization: str | None,
) -> tuple[dict[str, Any], Callable[[], Awaitable[None]] | None]:
    """FastAPI adapter: shared notification logic + ``HTTPException`` mapping."""
    try:
        return await process_notification(
            payload,
            settings=settings,
            jti_store=jti_store,
            jwks_client=jwks_client,
            mailbox=mailbox,
            authorization=authorization,
        )
    except ReceiverError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _dispatch_task_result(
    body: dict[str, Any],
    *,
    result_settings: ResultFetchSettings,
    result_http: httpx.AsyncClient,
) -> dict[str, Any]:
    """FastAPI adapter: shared result-fetch logic + ``HTTPException`` mapping."""
    try:
        return await process_task_result(
            body,
            result_settings=result_settings,
            result_http=result_http,
        )
    except ReceiverError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _verify_sender(
    settings: ReceiverSettings,
    jwks_client: JWKSClient,
    authorization: str,
) -> dict[str, Any]:
    """Verify the sender JWT, mapping failures to 401/502.

    Raises:
        ReceiverError 401: Sender verification failed (bad signature,
            unknown kid, expired/stale, missing jti).
        ReceiverError 502: The JWKS key fetch itself failed (unreachable
            endpoint, non-2xx, malformed body) -- an upstream key-service
            problem, not a sender defect.
    """
    try:
        return await verify_sender_jwt(
            authorization,
            jwks_client=jwks_client,
            audience=settings.receiver_url,
            issuer=settings.subagent_issuer,
            iat_staleness_seconds=settings.iat_staleness_seconds,
        )
    except SenderAuthError as exc:
        raise ReceiverError(status_code=401, detail=str(exc)) from exc
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise ReceiverError(status_code=502, detail=f"sender key fetch failed: {exc}") from exc


async def _forward(
    mailbox: NotificationMailbox,
    parent: Any,
    task_id: str,
    state: str,
    summary: Any,
    sender_iss: str,
) -> None:
    """Write the notice into the mailbox, then wake an idle supervisor.

    Runs in a ``BackgroundTasks`` after the ``202`` ACK so the sender's
    webhook timeout (10-30s per spec) is never consumed by Store/Agent
    Server round trips.

    Args:
        mailbox: The notification mailbox.
        parent: Unsealed callback token (routing target).
        task_id: A2A task id from the notification.
        state: Terminal run state.
        summary: Optional short result summary.
        sender_iss: The ``iss`` claim from the verified sender JWT
            (identifies the subagent deployment).
    """
    try:
        await mailbox.deliver(
            parent,
            task_id,
            state,
            summary=str(summary or "") or None,
            extra={"iss": sender_iss},
        )
        await mailbox.trigger_if_idle(parent)
    except MailboxWriteError as exc:
        # ACK already sent; the at-least-once semantics means the sender
        # may retry, but we must not let the background failure go silent.
        log.error("a2a_forward_failed", task_id=task_id, error=str(exc))
    except MailboxWakeError as exc:
        # The mailbox write succeeded but the supervisor was not woken --
        # the completion is stored but may never be drained.  This is a
        # delivery failure, not a benign retry condition: surface it.
        log.error(
            "a2a_forward_wake_failed",
            task_id=task_id,
            thread_id=parent.thread_id,
            error=str(exc),
        )

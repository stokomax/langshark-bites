"""A2A completion notifier -- push notifications between A2A deployments.

A bite for LangGraph multi-agent systems split across two Agent Server
deployments (supervisor on one, subagents on another).  LangChain does not
implement A2A push notifications, so this package supplies both missing
halves:

- **Emitter** (``middleware``) -- subagent-side ``AgentMiddleware`` that
  signs and POSTs an A2A completion notification when the subagent run
  reaches a terminal state.  Place on the subagent graph; LangChain's
  Agent Server would otherwise never push a completion webhook.
- **Receiver** (``receiver``) -- supervisor-side FastAPI receiver that
  terminates the A2A webhook, authenticates the sender, deduplicates,
  unseals the callback token, and delivers via the mailbox/drain pattern.

Quick start
-----------
Emitter (subagent graph, built as a dynamic factory)::

    from langshark_bites.a2a_completion_notifier.middleware import (
        build_a2a_notifier_from_config,
    )
    from langshark_bites.a2a_completion_notifier.push_client import PushClient
    from langshark_bites.a2a_completion_notifier.signer import A2ASigner

    def make_graph(config):
        notifier = build_a2a_notifier_from_config(
            config,
            signer=A2ASigner(pem, kid, issuer, audience),
            push_client=PushClient(),
        )
        return create_agent(model=..., tools=..., middleware=[notifier])

Receiver (supervisor side)::

    from langshark_bites.a2a_completion_notifier.receiver import create_receiver_app
    from langshark_bites.a2a_completion_notifier.settings import ReceiverSettings

    app = create_receiver_app(settings=ReceiverSettings.from_env())
"""

from __future__ import annotations

from langshark_bites.a2a_completion_notifier.auth import (
    ExpiredTokenError,
    InvalidSignatureError,
    JWKSClient,
    MissingBearerTokenError,
    MissingJtiError,
    SenderAuthError,
    StaleTokenError,
    UnknownKidError,
    verify_sender_jwt,
)
from langshark_bites.a2a_completion_notifier.drain import (
    MAILBOX_NAMESPACE_PREFIX,
    MailboxDrainError,
    MailboxDrainMiddleware,
    render_notice,
)
from langshark_bites.a2a_completion_notifier.idempotency import (
    InMemoryJtiStore,
    JtiStore,
    RedisJtiStore,
)
from langshark_bites.a2a_completion_notifier.mailbox import (
    MailboxWakeError,
    MailboxWriteError,
    NotificationMailbox,
)
from langshark_bites.a2a_completion_notifier.middleware import (
    A2APushNotifierMiddleware,
    PushEmissionError,
    PushNotificationConfig,
    PushNotificationConfigError,
    build_a2a_notifier_from_config,
    extract_push_config,
)
from langshark_bites.a2a_completion_notifier.payload import (
    TERMINAL_STATES,
    NotificationPayloadError,
    build_notification,
    extract_callback_token,
    extract_context_id,
    extract_summary,
    extract_task_id,
    extract_terminal_state,
    normalize_notification_payload,
    parse_a2a_task,
    task_to_dict,
)
from langshark_bites.a2a_completion_notifier.push_client import (
    PushClient,
    PushClientSettings,
)
from langshark_bites.a2a_completion_notifier.push_config import build_push_config
from langshark_bites.a2a_completion_notifier.receiver import create_receiver_app
from langshark_bites.a2a_completion_notifier.result_fetch import (
    ResultFetchSettings,
    TaskResultError,
    fetch_task_result,
)
from langshark_bites.a2a_completion_notifier.settings import (
    A2AVerifyMode,
    EmitterSettings,
    ReceiverSettings,
)
from langshark_bites.a2a_completion_notifier.signer import A2ASigner
from langshark_bites.a2a_completion_notifier.supervisor_tools import (
    create_supervisor_tools,
    prewarm_supervisor_tools,
)
from langshark_bites.a2a_completion_notifier.tokens import (
    CallbackToken,
    CallbackTokenError,
    CallbackTokenExpiredError,
    CallbackTokenInvalidError,
    mint_callback_token,
    unseal_callback_token,
)

__all__ = [
    "A2AVerifyMode",
    "A2APushNotifierMiddleware",
    "A2ASigner",
    "build_a2a_notifier_from_config",
    "build_notification",
    "build_push_config",
    "CallbackToken",
    "CallbackTokenError",
    "CallbackTokenExpiredError",
    "CallbackTokenInvalidError",
    "create_receiver_app",
    "create_supervisor_tools",
    "EmitterSettings",
    "ExpiredTokenError",
    "extract_callback_token",
    "extract_context_id",
    "extract_push_config",
    "extract_summary",
    "extract_task_id",
    "extract_terminal_state",
    "normalize_notification_payload",
    "NotificationPayloadError",
    "parse_a2a_task",
    "task_to_dict",
    "fetch_task_result",
    "InMemoryJtiStore",
    "InvalidSignatureError",
    "JtiStore",
    "JWKSClient",
    "MAILBOX_NAMESPACE_PREFIX",
    "MailboxDrainError",
    "MailboxDrainMiddleware",
    "MailboxWakeError",
    "MailboxWriteError",
    "mint_callback_token",
    "MissingBearerTokenError",
    "MissingJtiError",
    "NotificationMailbox",
    "prewarm_supervisor_tools",
    "PushClient",
    "PushClientSettings",
    "PushEmissionError",
    "PushNotificationConfig",
    "PushNotificationConfigError",
    "ReceiverSettings",
    "RedisJtiStore",
    "render_notice",
    "ResultFetchSettings",
    "SenderAuthError",
    "StaleTokenError",
    "TaskResultError",
    "TERMINAL_STATES",
    "UnknownKidError",
    "unseal_callback_token",
    "verify_sender_jwt",
]

"""Opaque callback tokens (HS256 JWT) for the completion notifier.

Why this exists
---------------
When the supervisor dispatches work to a subagent it must later tell the
receiver *which supervisor thread + assistant* a completion belongs to --
without leaking those details to the subagent server.  The token is
minted at dispatch time, travels as the opaque ``PushNotificationConfig.token``,
and is echoed back inside the A2A notification payload's ``metadata``.  The
receiver unseals it to learn the routing target.

Because the token is signed (HS256) with a secret only the supervisor
deployment holds, a subagent cannot read it, cannot forge a different
routing, and cannot redirect a notification to another thread.  The ``exp``
claim bounds the token's lifetime so a stale subagent cannot inject into a
thread days later.

Usage
-----
    from langshark_bites.a2a_completion_notifier.tokens import (
        mint_callback_token,
        unseal_callback_token,
    )

    token = mint_callback_token(
        thread_id="t1", assistant_id="asst_1", dispatch_id="d1",
        secret="supervisor-secret",
    )
    parent = unseal_callback_token(token, "supervisor-secret")
    assert parent.thread_id == "t1"
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt


class CallbackTokenError(Exception):
    """Base error for callback-token minting/unsealing (receiver side)."""


class CallbackTokenInvalidError(CallbackTokenError):
    """The token is malformed, invalidly signed, or missing required claims."""


class CallbackTokenExpiredError(CallbackTokenError):
    """The token's ``exp`` has passed; it can no longer be used for routing."""


_CALLBACK_CLAIMS = ("thread_id", "assistant_id", "dispatch_id")


@dataclass(frozen=True)
class CallbackToken:
    """Decoded routing payload carried inside an opaque callback token.

    Attributes:
        thread_id: Supervisor thread the completion must be routed to.
        assistant_id: Supervisor assistant to create the wake-up run against.
        dispatch_id: Correlation id for the whole fan-out dispatch.
        exp: Unix timestamp when the token expires.
    """

    thread_id: str
    assistant_id: str
    dispatch_id: str
    exp: int


def mint_callback_token(
    thread_id: str,
    assistant_id: str,
    dispatch_id: str,
    secret: str,
    *,
    ttl_seconds: int = 86400,
    now: int | None = None,
) -> str:
    """Mint a signed, opaque callback token (HS256).

    Args:
        thread_id: Supervisor thread id to encode.
        assistant_id: Supervisor assistant id to encode.
        dispatch_id: Correlation id for the dispatch.
        secret: ``CALLBACK_TOKEN_SECRET`` -- never sent to the subagent side.
        ttl_seconds: Lifetime of the token.  Default 24h.
        now: Override the current time (tests).

    Returns:
        The signed JWT string, safe to hand to the subagent as an opaque
        value in ``PushNotificationConfig.token``.
    """
    now = int(now if now is not None else time.time())
    return jwt.encode(
        {
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "dispatch_id": dispatch_id,
            "iat": now,
            "exp": now + ttl_seconds,
        },
        secret,
        algorithm="HS256",
    )


def unseal_callback_token(token: str, secret: str) -> CallbackToken:
    """Verify and decode a callback token signed with ``secret``.

    Args:
        token: The opaque JWT echoed back by the subagent.
        secret: The same ``CALLBACK_TOKEN_SECRET`` used at mint time.

    Returns:
        The decoded routing payload.

    Raises:
        CallbackTokenExpiredError: Token ``exp`` has passed.
        CallbackTokenInvalidError: Bad signature, malformed token, or a
            missing required claim.
    """
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise CallbackTokenExpiredError("callback token expired") from exc
    except jwt.PyJWTError as exc:
        raise CallbackTokenInvalidError("callback token could not be decoded") from exc

    missing = [claim for claim in _CALLBACK_CLAIMS if not claims.get(claim)]
    if missing:
        raise CallbackTokenInvalidError(f"callback token missing claims: {', '.join(missing)}")
    return CallbackToken(
        thread_id=str(claims["thread_id"]),
        assistant_id=str(claims["assistant_id"]),
        dispatch_id=str(claims["dispatch_id"]),
        exp=int(claims["exp"]),
    )

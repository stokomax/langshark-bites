"""Supervisor-side push-config helpers: mint the token and build the config.

Why this exists
---------------
The supervisor must, at dispatch time, mint an opaque callback token and
hand it to the subagent alongside the webhook URL, so the subagent's
emission middleware can notify the receiver::

    config.configurable["a2a_push_config"] = {
        "url": f"{RECEIVER_URL}/a2a/notifications",
        "token": <minted callback token>,
        "authentication": {"schemes": ["Bearer"]},
    }

``build_push_config`` wraps that exact construction so a supervisor graph
does not need to know the wire format.  The token is signed with the
supervisor-side secret and carries ``thread_id``/``assistant_id``/
``dispatch_id``; the subagent echoes it back uninterpreted and the receiver
unseals it to route.

Usage
-----
    from langshark_bites.a2a_completion_notifier.push_config import (
        build_push_config,
    )

    config = build_push_config(
        thread_id="thread_1",
        assistant_id="supervisor_asst",
        dispatch_id="dispatch_1",
        receiver_url="https://receiver.example",
        callback_token_secret="supervisor-secret",
    )
    # pass config into the subagent run:
    #   await lg.runs.create(thread_id=..., assistant_id=...,
    #                        input=..., config=config)
"""

from __future__ import annotations

from typing import Any

from langshark_bites.a2a_completion_notifier.settings import A2AVerifyMode
from langshark_bites.a2a_completion_notifier.tokens import mint_callback_token


def build_push_config(
    *,
    thread_id: str,
    assistant_id: str,
    dispatch_id: str,
    receiver_url: str,
    callback_token_secret: str,
    callback_token_ttl_seconds: int = 86400,
    mode: A2AVerifyMode = A2AVerifyMode.DEV,
    now: int | None = None,
) -> dict[str, Any]:
    """Build the ``config.configurable`` push config for one subagent.

    Args:
        thread_id: Supervisor thread the completion must be routed to.
        assistant_id: Supervisor assistant to wake when the subagent finishes.
        dispatch_id: Correlation id for the whole fan-out dispatch.
        receiver_url: Public base URL of the supervisor's receiver (the
            ``/a2a/notifications`` path is appended automatically).
        callback_token_secret: ``CALLBACK_TOKEN_SECRET`` (supervisor-side
            only; never sent to the subagent).
        callback_token_ttl_seconds: Lifetime of the callback token.
        mode: The supervisor's ``A2A_VERIFY_MODE`` at dispatch time.  Stamped into
            the push config so the emitter can fail fast when a ``strict``
            dispatch demands signing but the deployment has no key.
        now: Override the current time (tests).

    Returns:
        A dict suitable for ``config.configurable`` on the subagent run.  The
        subagent's ``build_a2a_notifier_from_config`` reads it and wires the
        emitter middleware automatically.
    """
    token = mint_callback_token(
        thread_id=thread_id,
        assistant_id=assistant_id,
        dispatch_id=dispatch_id,
        secret=callback_token_secret,
        ttl_seconds=callback_token_ttl_seconds,
        now=now,
    )
    return {
        "configurable": {
            "a2a_push_config": {
                "url": f"{receiver_url.rstrip('/')}/a2a/notifications",
                "token": token,
                # A2A Task.contextId — supervisor thread is the natural context.
                "context_id": thread_id,
                "authentication": {"schemes": ["Bearer"]},
                "mode": mode.value,
            }
        }
    }

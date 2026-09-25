"""Idempotency for incoming A2A notifications (``jti`` single-use claim).

Why this exists
---------------
A2A delivery is at-least-once: the subagent server retries its webhook POST
with backoff until it gets an ACK.  If the receiver accepted a notification
but the ACK was lost, the sender retries and the receiver sees the *same*
notification twice.  Without dedup, a redelivered completion injects a
second completion message into the supervisor's history -- which the
supervisor may read as two completions for one task (the re-dispatch loop).

The A2A JWT carries a unique ``jti``.  This module claims it exactly once
per receiver lifetime window, so retries collapse to a no-op.

Why Redis and not the Store
---------------------------
``jti`` claims are receiver infrastructure state, not conversation state:
they need a hard TTL, cross-replica atomicity, and sub-millisecond latency
on the ACK path.  ``SET NX EX`` gives all three in one round trip.  A
LangGraph ``Store`` has no native key expiry and check-then-write is two
round trips -- wrong tool for this job.

The in-memory store is a single-process fallback for tests and prototypes;
it is NOT safe across receiver replicas.

Usage
-----
    from langshark_bites.a2a_completion_notifier.idempotency import RedisJtiStore

    store = RedisJtiStore(redis_client, ttl_seconds=900)
    is_new = await store.claim("jti-abc")   # True once, then False
"""

from __future__ import annotations

import time
from typing import Any, Protocol


class JtiStore(Protocol):
    """A single-use claim store for A2A notification ``jti`` values."""

    async def claim(self, jti: str) -> bool:
        """Return True the first time ``jti`` is claimed, False afterwards."""
        ...


class RedisJtiStore:
    """Redis-backed ``jti`` claim store using atomic ``SET NX EX``.

    Attributes:
        ttl_seconds: How long a claimed ``jti`` is remembered.  Must exceed
            the sender's total retry window (spec: 10s HTTP timeout + retry
            budget); 900s (15 min) covers most retry policies.
    """

    def __init__(self, redis: Any, *, ttl_seconds: int = 900) -> None:
        """Store ``jti`` claims in ``redis``, expiring them after ``ttl_seconds``."""
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    async def claim(self, jti: str) -> bool:
        """Attempt to claim ``jti`` exactly once.

        ``SET key 1 NX EX ttl`` returns a truthy value only if the key did
        not previously exist -- check-and-write is a single atomic op, so
        two receiver replicas cannot both claim the same ``jti``.

        Args:
            jti: The unique JWT ID from the incoming notification.

        Returns:
            True if this is the first time ``jti`` was seen; False on a
            redelivery/duplicate.
        """
        created = await self._redis.set(f"a2a:jti:{jti}", "1", nx=True, ex=self._ttl_seconds)
        return bool(created)


class InMemoryJtiStore:
    """Single-process ``jti`` store for tests and prototypes.

    Keeps an expiry map instead of a growing set so long-lived processes do
    not accumulate stale keys.  Not safe across receiver replicas -- use
    ``RedisJtiStore`` in production.
    """

    def __init__(self, *, ttl_seconds: int = 900) -> None:
        """Track ``jti`` claims in-process, expiring them after ``ttl_seconds``."""
        self._ttl_seconds = ttl_seconds
        self._expires: dict[str, float] = {}

    async def claim(self, jti: str) -> bool:
        """Attempt to claim ``jti`` exactly once within this process.

        Args:
            jti: The unique JWT ID from the incoming notification.

        Returns:
            True if this is the first time ``jti`` was seen; False on a
            redelivery/duplicate.
        """
        now = time.monotonic()
        self._expires = {k: v for k, v in self._expires.items() if v > now}
        if jti in self._expires:
            return False
        self._expires[jti] = now + self._ttl_seconds
        return True

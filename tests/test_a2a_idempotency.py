"""Tests for langshark_bites.a2a_completion_notifier.idempotency."""

from __future__ import annotations

from langshark_bites.a2a_completion_notifier.idempotency import (
    InMemoryJtiStore,
    RedisJtiStore,
)


class TestRedisJtiStore:
    async def test_first_claim_true_then_false(self):
        import fakeredis

        redis = fakeredis.aioredis.FakeRedis()
        store = RedisJtiStore(redis, ttl_seconds=900)
        assert await store.claim("jti-1") is True
        assert await store.claim("jti-1") is False

    async def test_distinct_jti_claimable(self):
        import fakeredis

        redis = fakeredis.aioredis.FakeRedis()
        store = RedisJtiStore(redis, ttl_seconds=900)
        assert await store.claim("jti-a") is True
        assert await store.claim("jti-b") is True

    async def test_claim_uses_nx_ttl(self):
        import fakeredis

        redis = fakeredis.aioredis.FakeRedis()
        store = RedisJtiStore(redis, ttl_seconds=120)
        await store.claim("jti-x")
        # Key must exist with a TTL, proving SET NX EX semantics.
        ttl = await redis.ttl("a2a:jti:jti-x")
        assert 0 < ttl <= 120

    async def test_expired_jti_reclaimable(self):
        import fakeredis

        redis = fakeredis.aioredis.FakeRedis()
        store = RedisJtiStore(redis, ttl_seconds=10)
        assert await store.claim("jti-z") is True
        # Simulate expiry by removing the key (Redis EX 0 is invalid).
        await redis.delete("a2a:jti:jti-z")
        assert await store.claim("jti-z") is True


class TestInMemoryJtiStore:
    async def test_exactly_once(self):
        store = InMemoryJtiStore(ttl_seconds=900)
        assert await store.claim("j-1") is True
        assert await store.claim("j-1") is False

    async def test_different_jti_ok(self):
        store = InMemoryJtiStore(ttl_seconds=900)
        assert await store.claim("j-a") is True
        assert await store.claim("j-b") is True

    async def test_expiry_resets(self):
        store = InMemoryJtiStore(ttl_seconds=0)
        assert await store.claim("j-2") is True
        assert await store.claim("j-2") is True

"""Tests for the Redis-backed distributed rate limiter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from langshark_bites.api_rate_limiter import (
    RateLimitConfig,
    RateLimiter,
    load_provider_configs,
    rate_limited,
)


class TestRateLimitConfig:
    """Tests for ``RateLimitConfig`` construction."""

    def test_from_rpm_defaults(self):
        cfg = RateLimitConfig.from_rpm("test", requests_per_minute=60)
        assert cfg.name == "test"
        assert cfg.capacity == 60  # burst defaults to requests_per_minute
        assert cfg.refill_rate == 1.0  # 60 / 60
        assert cfg.acquire_timeout == 30.0
        assert cfg.max_concurrent is None

    def test_from_rpm_with_burst(self):
        cfg = RateLimitConfig.from_rpm("test", requests_per_minute=60, burst=20)
        assert cfg.capacity == 20
        assert cfg.refill_rate == 1.0

    def test_from_rpm_with_max_concurrent(self):
        cfg = RateLimitConfig.from_rpm(
            "test", requests_per_minute=60, max_concurrent=5
        )
        assert cfg.max_concurrent == 5


class TestLoadProviderConfigs:
    """Tests for ``load_provider_configs()``."""

    def test_returns_empty_when_nothing_configured(self, monkeypatch):
        """No YAML, no env vars → no providers (generalized config)."""
        monkeypatch.delenv("RATE_LIMIT_NEWSAPI_RPM", raising=False)
        monkeypatch.delenv("RATE_LIMIT_NEWSAPI_BURST", raising=False)
        configs = load_provider_configs(path="/nonexistent/path/rate_limits.yaml")
        assert configs == {}

    def test_env_var_defines_provider(self, monkeypatch):
        """Env var defines a provider (no baked-in defaults)."""
        monkeypatch.setenv("RATE_LIMIT_NEWSAPI_RPM", "200")
        monkeypatch.setenv("RATE_LIMIT_NEWSAPI_BURST", "50")
        configs = load_provider_configs(path="/nonexistent/path/rate_limits.yaml")
        assert "newsapi" in configs
        assert configs["newsapi"].capacity == 50
        assert configs["newsapi"].refill_rate == 200 / 60.0

    def test_yaml_defines_provider(self, tmp_path, monkeypatch):
        """YAML file defines a provider."""
        yaml_file = tmp_path / "rate_limits.yaml"
        yaml_file.write_text(
            "providers:\n"
            "  openai:\n"
            "    requests_per_minute: 60\n"
            "    burst: 10\n"
            "    max_concurrent: 5\n"
        )
        configs = load_provider_configs(path=str(yaml_file))
        assert "openai" in configs
        assert configs["openai"].capacity == 10
        assert configs["openai"].refill_rate == 1.0
        assert configs["openai"].max_concurrent == 5


class TestRateLimiter:
    """Tests for ``RateLimiter`` — local fallback path (no Redis)."""

    def test_acquire_unknown_provider_valueerror(self):
        limiter = RateLimiter(redis_url="redis://localhost:6379/0", configs={})
        with pytest.raises(ValueError, match="No rate limit config"):
            asyncio.run(_acquire_and_release(limiter, "unknown_provider"))

    @pytest.mark.asyncio
    async def test_local_fallback_acquires_token(self):
        """With no Redis, the in-process fallback should grant tokens immediately
        on first call (bucket starts full)."""
        config = RateLimitConfig.from_rpm("test", requests_per_minute=120)
        limiter = RateLimiter(
            redis_url="redis://localhost:6379/0", configs={"test": config}
        )
        # Patch _ensure_redis to always return False (force local fallback)
        limiter._ensure_redis = AsyncMock(return_value=False)  # type: ignore[method-assign]

        async with limiter.acquire("test"):
            pass  # Should not raise

    @pytest.mark.asyncio
    async def test_local_fallback_exhausts_burst(self):
        """After consuming all burst tokens, further acquires should block."""
        burst = 3
        config = RateLimitConfig.from_rpm("test", requests_per_minute=60, burst=burst)
        limiter = RateLimiter(
            redis_url="redis://localhost:6379/0", configs={"test": config}
        )
        limiter._ensure_redis = AsyncMock(return_value=False)  # type: ignore[method-assign]

        # Acquire burst tokens — should all succeed immediately
        for _ in range(burst):
            async with limiter.acquire("test"):
                pass

        # Next acquire should need to wait (bucket empty), but with real ticks
        # it'll refill. Just verify the bucket tracked depletion.
        assert limiter._local_tokens["test"] < 1.0

    @pytest.mark.asyncio
    async def test_rate_limited_decorator(self):
        """The @rate_limited decorator wraps and calls through."""
        config = RateLimitConfig.from_rpm("test", requests_per_minute=120)
        limiter = RateLimiter(
            redis_url="redis://localhost:6379/0", configs={"test": config}
        )
        limiter._ensure_redis = AsyncMock(return_value=False)  # type: ignore[method-assign]

        call_count = 0

        @rate_limited(limiter, provider="test")
        async def dummy() -> int:
            nonlocal call_count
            call_count += 1
            return 42

        result = await dummy()
        assert result == 42
        assert call_count == 1


async def _acquire_and_release(limiter: RateLimiter, provider: str) -> None:
    async with limiter.acquire(provider):
        pass


class TestRateLimiterFromEnv:
    """Tests for ``RateLimiter.from_env()``."""

    def test_creates_with_no_providers(self):
        limiter = RateLimiter.from_env(
            redis_url="redis://localhost:6379/0",
            config_path="/nonexistent/path/rate_limits.yaml",
        )
        assert limiter._configs == {}
        assert limiter._redis_url == "redis://localhost:6379/0"

    def test_env_redis_url(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://custom:6380/1")
        limiter = RateLimiter.from_env(
            config_path="/nonexistent/path/rate_limits.yaml",
        )
        assert limiter._redis_url == "redis://custom:6380/1"

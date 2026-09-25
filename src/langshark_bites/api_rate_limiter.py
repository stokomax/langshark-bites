"""Distributed rate limiting for external API calls made by LangGraph subagents.

Why this exists
---------------
When multiple Agent Server replicas (or multiple parallel Send-dispatched
subagent nodes within one superstep) all call the same external API, a
per-process asyncio.Semaphore is NOT sufficient: each process gets its own
independent budget, so your effective rate limit becomes
(configured_limit * number_of_replicas), which defeats the purpose.

This module keeps the bucket state in Redis, shared across every process
that imports it, and updates it atomically via a Lua script so concurrent
callers across different workers never race on read-modify-write.

Algorithm: token bucket. Chosen deliberately over leaky-bucket smoothing
because most external API rate limit docs are phrased as "N requests per
window with burst up to N" (token bucket semantics), not "steady drip" --
match the algorithm to how the provider actually enforces its own limit.

Usage
-----
    from langshark_bites.api_rate_limiter import RateLimiter, rate_limited

    limiter = RateLimiter.from_env()

    @rate_limited(limiter, provider="newsapi")
    async def fetch_news(ticker: str):
        ...

Or use the context manager directly:

    async with limiter.acquire("newsapi"):
        resp = await client.get(...)

Configuration
-------------
Providers are defined in a YAML file (preferred) or via environment
variables.  There are NO baked-in providers — you define the ones your
project needs.  See ``rate_limits.example.yaml`` for the schema.

    RATE_LIMIT_CONFIG_PATH=/path/to/rate_limits.yaml
        (default: ~/.config/langshark_bites/rate_limits.yaml)

    # Or per-provider env vars (override YAML):
    RATE_LIMIT_NEWSAPI_RPM=100
    RATE_LIMIT_NEWSAPI_BURST=20

Redis connection
----------------
    REDIS_URL=redis://localhost:6379/0   (default)

If Redis is unreachable, falls back to in-process asyncio.Semaphore --
rate limiting still works per-process (no cross-replica coordination
but the system stays up).
"""

from __future__ import annotations

import asyncio
import functools
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

log = structlog.get_logger(__name__)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Embedded defaults -- intentionally empty.  Users define their own providers
# via YAML or env vars (see module docstring and rate_limits.example.yaml).
# ---------------------------------------------------------------------------

DEFAULT_PROVIDER_CONFIGS: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitConfig:
    """Configuration for a single external API's rate limit."""

    name: str  # provider key, e.g. "newsapi"
    capacity: int  # max burst size (tokens in a full bucket)
    refill_rate: float  # tokens added per second
    acquire_timeout: float = 30.0  # max seconds a caller will wait for a token
    max_concurrent: int | None = None  # optional extra concurrency cap
    source: str = ""  # provenance: "built-in default", "env: ...", or "YAML: ..."

    @classmethod
    def from_rpm(
        cls,
        name: str,
        requests_per_minute: int,
        burst: int | None = None,
        acquire_timeout: float = 30.0,
        max_concurrent: int | None = None,
        source: str = "",
    ) -> RateLimitConfig:
        """Convenience constructor from 'N requests per minute' spec."""
        return cls(
            name=name,
            capacity=burst or requests_per_minute,
            refill_rate=requests_per_minute / 60.0,
            acquire_timeout=acquire_timeout,
            max_concurrent=max_concurrent,
            source=source,
        )


def _load_yaml_providers(path: str) -> dict[str, Any]:
    """Read the ``providers`` map from a YAML rate-limit config file."""
    config_path = Path(path)
    if not config_path.exists():
        log.info("rate_limit_config_missing", path=path)
        return {}
    try:
        import yaml

        with config_path.open() as f:
            data = yaml.safe_load(f) or {}
        yaml_config = data.get("providers", {}) or {}
        if not yaml_config:
            log.warning("rate_limit_config_empty", path=path)
        return yaml_config
    except Exception:
        log.error("rate_limit_config_parse_error", path=path, exc_info=True)
        return {}


def _discover_provider_names(yaml_config: dict[str, Any]) -> set[str]:
    """Union of default, YAML, and env-declared provider names."""
    names: set[str] = set(DEFAULT_PROVIDER_CONFIGS.keys())
    names.update(yaml_config.keys())
    for key in os.environ:
        if key.startswith("RATE_LIMIT_") and key.endswith("_RPM"):
            provider = key[len("RATE_LIMIT_") : -len("_RPM")].lower()
            names.add(provider)
    return names


def _config_for_provider(
    name: str,
    *,
    yaml_config: dict[str, Any],
    path: str,
) -> RateLimitConfig | None:
    """Resolve one provider's RateLimitConfig from env > YAML > defaults."""
    yaml_spec = yaml_config.get(name, {})
    env_rpm = os.environ.get(f"RATE_LIMIT_{name.upper()}_RPM")
    env_burst = os.environ.get(f"RATE_LIMIT_{name.upper()}_BURST")
    env_concurrency = os.environ.get(f"RATE_LIMIT_{name.upper()}_CONCURRENCY")
    default_spec = DEFAULT_PROVIDER_CONFIGS.get(name, {})

    source = "built-in default"
    if env_rpm:
        rpm = int(env_rpm)
        source = f"env: RATE_LIMIT_{name.upper()}_RPM={env_rpm}"
    elif name in yaml_config:
        rpm = yaml_spec.get(
            "requests_per_minute",
            default_spec.get("requests_per_minute", 0),
        )
        source = f"YAML: {path}"
    else:
        rpm = default_spec.get("requests_per_minute", 0)

    if rpm <= 0:
        log.warning("rate_limit_disabled", provider=name)
        return None

    burst_str = env_burst or yaml_spec.get("burst", default_spec.get("burst"))
    burst = int(burst_str) if burst_str else None

    conc_str = env_concurrency or yaml_spec.get(
        "max_concurrent", default_spec.get("max_concurrent")
    )
    max_concurrent = int(conc_str) if conc_str else None

    return RateLimitConfig.from_rpm(
        name=name,
        requests_per_minute=rpm,
        burst=burst,
        acquire_timeout=yaml_spec.get("acquire_timeout", 30.0),
        max_concurrent=max_concurrent,
        source=source,
    )


def load_provider_configs(
    path: str | None = None,
) -> dict[str, RateLimitConfig]:
    """Load provider rate-limit configs from a YAML file (preferred) or env vars.

    Resolution order:
    1. YAML file at *path* (or RATE_LIMIT_CONFIG_PATH env, or
       ~/.config/langshark_bites/rate_limits.yaml).
    2. Environment variables: RATE_LIMIT_<PROVIDER>_RPM, _BURST, _CONCURRENCY.
    3. Embedded DEFAULT_PROVIDER_CONFIGS (empty by default).
    """
    path = path or os.environ.get(
        "RATE_LIMIT_CONFIG_PATH",
        str(Path("~/.config/langshark_bites/rate_limits.yaml").expanduser()),
    )
    yaml_config = _load_yaml_providers(path)
    configs: dict[str, RateLimitConfig] = {}
    for name in sorted(_discover_provider_names(yaml_config)):
        cfg = _config_for_provider(name, yaml_config=yaml_config, path=path)
        if cfg is not None:
            configs[name] = cfg

    if not configs:
        log.warning("rate_limiter_no_config")

    return configs


# ---------------------------------------------------------------------------
# Atomic Redis token bucket (Lua script)
# ---------------------------------------------------------------------------

# KEYS[1] = bucket key
# ARGV[1] = capacity, ARGV[2] = refill_rate (tokens/sec),  # noqa: ERA001
# ARGV[3] = now (ms, float-safe as string), ARGV[4] = requested tokens
_TOKEN_BUCKET_LUA = """
local capacity   = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now        = tonumber(ARGV[3])
local requested  = tonumber(ARGV[4])

local bucket = redis.call("HMGET", KEYS[1], "tokens", "ts")
local tokens = tonumber(bucket[1])
local last_ts = tonumber(bucket[2])

if tokens == nil then
  tokens = capacity
  last_ts = now
end

local delta = math.max(0, now - last_ts)
tokens = math.min(capacity, tokens + (delta * refill_rate / 1000.0))

local allowed = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
end

redis.call("HMSET", KEYS[1], "tokens", tokens, "ts", now)
redis.call("EXPIRE", KEYS[1], math.ceil(capacity / refill_rate) + 60)

return {allowed, tostring(tokens)}
"""


class RateLimiter:
    """Distributed, per-provider token bucket rate limiter backed by Redis.

    One instance serves every provider; each provider's bucket state is
    keyed independently in Redis. Safe to construct once at module import
    time and reuse across every subagent and worker process.
    """

    #: Debounce interval for ``rate_limit_throttled`` WARNING (seconds).
    _THROTTLE_WARN_INTERVAL: float = 10.0

    def __init__(self, redis_url: str, configs: dict[str, RateLimitConfig]) -> None:
        """Build a limiter backed by ``redis_url`` using per-provider ``configs``."""
        self._redis_url = redis_url
        self._configs = configs
        self._redis: Any = None
        self._script_sha: str | None = None
        self._redis_ok: bool = True
        self._redis_warned: bool = False
        self._init_lock = asyncio.Lock()

        # In-process fallback semaphores
        self._local_semaphores: dict[str, asyncio.Semaphore] = {
            name: asyncio.Semaphore(cfg.max_concurrent or cfg.capacity)
            for name, cfg in configs.items()
        }

        # Track when each provider last successfully acquired a local token
        # for the fallback rate-limiting (refill simulation)
        self._local_last_acquire: dict[str, float] = {}
        self._local_tokens: dict[str, float] = {
            name: float(cfg.capacity) for name, cfg in configs.items()
        }

        # Debounce throttle warnings: at most one per interval per provider
        self._last_throttle_warned: dict[str, float] = {}

    @classmethod
    def from_env(
        cls,
        redis_url: str | None = None,
        config_path: str | None = None,
    ) -> RateLimiter:
        """Factory: read config from env/defaults and return a RateLimiter."""
        redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        configs = load_provider_configs(config_path)
        instance = cls(redis_url=redis_url, configs=configs)
        if configs:
            names = ", ".join(f"{cfg.name}({cfg.capacity}/min)" for cfg in configs.values())
            log.info("rate_limiter_configured", providers=names)
        return instance

    async def _ensure_redis(self) -> bool:
        """Lazily connect to Redis. Returns True if Redis is available."""
        if self._redis is not None:
            return self._redis_ok

        async with self._init_lock:
            if self._redis is not None:
                return self._redis_ok

            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
                # Load the Lua script once
                self._script_sha = await self._redis.script_load(_TOKEN_BUCKET_LUA)
                # Verify connectivity
                await self._redis.ping()
                self._redis_ok = True
                log.info("rate_limiter_redis_connected", url=self._redis_url)
                return True
            except Exception:
                self._redis_ok = False
                if not self._redis_warned:
                    log.error(
                        "rate_limiter_redis_unavailable",
                        url=self._redis_url,
                        exc_info=True,
                    )
                    self._redis_warned = True
                return False

    def config_summary(self, provider: str) -> dict[str, object] | None:
        """Return a dict of key config fields for *provider*, or None if unconfigured.

        Designed for use in followed-up log lines (e.g. ``rate_limit_429_received.config``)
        so operators can see what limits were actually in effect when a 429 fired.
        """
        cfg = self._configs.get(provider)
        if cfg is None:
            return None
        return {
            "provider": cfg.name,
            "capacity": cfg.capacity,
            "rpm": int(cfg.refill_rate * 60),
            "refill_rate": round(cfg.refill_rate, 3),
            "acquire_timeout": cfg.acquire_timeout,
            "max_concurrent": cfg.max_concurrent,
            "source": cfg.source,
        }

    def acquire(self, provider: str) -> _AcquireContext:
        """Return an async context manager that acquires a rate-limit token.

        Usage::

            async with limiter.acquire("newsapi"):
                resp = await client.get(...)

        Raises ValueError immediately if *provider* is not configured.
        """
        config = self._configs.get(provider)
        if config is None:
            raise ValueError(
                f"No rate limit config for provider '{provider}'. "
                f"Available providers: "
                f"{', '.join(sorted(self._configs.keys())) or 'none'}"
            )

        return _AcquireContext(self, provider, config)

    async def _try_redis_acquire(
        self, provider: str, config: RateLimitConfig, start: float
    ) -> float | None:
        """Attempt one Redis token-bucket take. None means fall through to local."""
        if not (await self._ensure_redis() and self._script_sha is not None):
            return None
        try:
            now_ms = asyncio.get_event_loop().time() * 1000
            redis = self._redis
            assert redis is not None  # guarded by _ensure_redis
            result = await redis.evalsha(
                self._script_sha,
                1,
                f"rate_limit:{provider}",
                str(config.capacity),
                str(config.refill_rate),
                str(now_ms),
                "1",
            )
            if int(result[0]):
                return asyncio.get_event_loop().time() - start

            remaining = float(result[1])
            now_ts = asyncio.get_event_loop().time()
            last_warn = self._last_throttle_warned.get(provider, 0)
            if now_ts - last_warn >= self._THROTTLE_WARN_INTERVAL:
                wait_est = (1.0 - remaining) / config.refill_rate
                log.warning("rate_limit_throttled", provider=provider, wait=wait_est)
                self._last_throttle_warned[provider] = now_ts
            return None
        except Exception:
            self._redis_ok = False
            self._script_sha = None
            if not self._redis_warned:
                log.error("rate_limiter_redis_unavailable", exc_info=True)
                self._redis_warned = True
            return None

    async def _try_local_acquire(
        self,
        provider: str,
        config: RateLimitConfig,
        start: float,
        deadline: float,
        now_mono: float,
    ) -> float | None:
        """In-process token-bucket fallback. None means sleep-and-retry."""
        now = asyncio.get_event_loop().time()
        tokens = self._local_tokens.get(provider, float(config.capacity))
        last = self._local_last_acquire.get(provider, now)

        delta = max(0.0, now - last)
        tokens = min(float(config.capacity), tokens + delta * config.refill_rate)
        self._local_last_acquire[provider] = now

        if tokens >= 1.0:
            self._local_tokens[provider] = tokens - 1.0
            return asyncio.get_event_loop().time() - start

        self._local_tokens[provider] = tokens
        wait_sec = max(0.05, (1.0 - tokens) / config.refill_rate)
        wait_sec = min(wait_sec, deadline - now_mono)
        if wait_sec > 0:
            from langshark_bites.api_backoff import async_backoff

            await async_backoff(
                wait_sec,
                context=f"rate_limit:{provider} (in-process fallback)",
            )
        return None

    async def _acquire_token(self, provider: str, config: RateLimitConfig) -> float:
        """Acquire a token from Redis (or local fallback).

        Returns the wait time in seconds (0 if acquired immediately).
        Raises asyncio.TimeoutError if acquire_timeout is exceeded.
        """
        sem = self._local_semaphores.get(provider)
        if sem is not None and config.max_concurrent:
            await sem.acquire()

        start = asyncio.get_event_loop().time()
        deadline = start + config.acquire_timeout

        while True:
            now_mono = asyncio.get_event_loop().time()
            if now_mono >= deadline:
                if sem is not None and config.max_concurrent:
                    sem.release()
                log.error(
                    "rate_limit_exhausted",
                    provider=provider,
                    timeout=config.acquire_timeout,
                )
                raise TimeoutError(
                    f"Rate limit acquire timeout for '{provider}' "
                    f"after {config.acquire_timeout:.0f}s"
                )

            waited = await self._try_redis_acquire(provider, config, start)
            if waited is not None:
                return waited

            waited = await self._try_local_acquire(provider, config, start, deadline, now_mono)
            if waited is not None:
                return waited


class _AcquireContext:
    """Async context manager returned by RateLimiter.acquire()."""

    def __init__(self, limiter: RateLimiter, provider: str, config: RateLimitConfig) -> None:
        self._limiter = limiter
        self._provider = provider
        self._config = config

    async def __aenter__(self) -> None:
        await self._limiter._acquire_token(self._provider, self._config)

    async def __aexit__(self, *args: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def rate_limited(
    limiter: RateLimiter,
    provider: str,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator: wrap an async function with rate limiting.

    Usage::

        limiter = RateLimiter.from_env()

        @rate_limited(limiter, provider="newsapi")
        async def fetch_news(ticker: str) -> str:
            ...
    """

    def decorator(
        func: Callable[..., Awaitable[T]],
    ) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            async with limiter.acquire(provider):
                return await func(*args, **kwargs)

        return wrapper

    return decorator

"""Runtime configuration for the ``a2a_completion_notifier`` bite.

Why this exists
---------------
The completion notifier spans two deployments that must not share
credentials, so configuration is split into two dataclasses:

- ``ReceiverSettings`` -- the **supervisor** deployment.  Owns the callback
  token secret, the supervisor Agent Server URL + API key, the push
  webhook URL it places in ``PushNotificationConfig``, and (for fetching
  full results) the subagent Agent Server URL + API key.
- ``EmitterSettings`` -- the **subagent** deployment.  Owns the RS256
  private key + ``kid`` used to sign the outgoing A2A push JWT, and the
  retry/backoff policy for the webhook POST.

Environment variable conventions mirror ``api_rate_limiter``: uppercase,
``_``-separated, prefixed with ``A2A_``, resolved by ``from_env()``
classmethods.

Usage
-----
    from langshark_bites.a2a_completion_notifier.settings import ReceiverSettings

    receiver = ReceiverSettings.from_env()
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


class A2AVerifyMode(StrEnum):
    """Sender-verification mode for the receiver, set by the supervisor.

    Ordered by strictness; ``DEV < VERIFY < STRICT``.

    - ``dev`` -- trusted private network.  Only the callback token is
      checked; sender JWTs are accepted unverified.
    - ``verify`` -- verify-if-signed.  A signed notification is verified and
      fails closed (401).  An unsigned one is accepted (logged), so a fleet
      mid-migration tolerates not-yet-signed senders.
    - ``strict`` -- signing is mandatory.  Unsigned -> 401; signed but
      unverifiable -> 401; sender-auth config is a startup requirement.
    """

    DEV = "dev"
    VERIFY = "verify"
    STRICT = "strict"

    def __lt__(self, other: object) -> bool:
        """Order modes by strictness, so ``DEV < VERIFY < STRICT``."""
        if not isinstance(other, A2AVerifyMode):
            return NotImplemented
        order = ("dev", "verify", "strict")
        return order.index(self.value) < order.index(other.value)


def _env(name: str, default: str) -> str:
    """Read ``A2A_<NAME>`` from the environment with a fallback."""
    return os.environ.get(f"A2A_{name}", default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(f"A2A_{name}")
    if raw is None:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(f"A2A_{name}")
    if raw is None:
        return default
    return float(raw)


@dataclass(frozen=True)
class ReceiverSettings:
    """Supervisor-side receiver configuration.

    ``A2A_VERIFY_MODE`` selects the sender-verification posture (``dev`` /
    ``verify`` / ``strict``); see :class:`A2AVerifyMode`.  ``redis_url`` enables
    Redis-backed ``jti`` deduplication (atomic across receiver replicas).
    The dead-letter *tool* is read-only today -- nothing parks notifications
    in the queue yet (the receiver logs ``a2a_forward_failed`` /
    ``a2a_forward_wake_failed`` on undelivered deliveries instead of
    parking them).  All other fields except the numeric/timeouts are
    read from ``A2A_*`` env vars with the defaults shown.
    """

    supervisor_url: str = "http://localhost:8000"
    supervisor_api_key: str = ""
    callback_token_secret: str = ""
    receiver_url: str = "http://localhost:8001"
    subagent_issuer: str = ""
    subagent_jwks_url: str = ""
    subagent_url: str = ""
    subagent_api_key: str = ""
    receiver_port: int = 8001
    redis_url: str = ""
    jti_ttl_seconds: int = 900
    iat_staleness_seconds: int = 300
    callback_token_ttl_seconds: int = 86400
    mode: A2AVerifyMode = A2AVerifyMode.DEV

    def ensure_valid(self) -> None:
        """Fail fast on sender-auth misconfiguration for the chosen mode.

        Raises:
            ValueError: ``strict`` without sender-auth config, or ``verify``
                with partially populated sender-auth config.
        """
        jwks = self.subagent_jwks_url
        issuer = self.subagent_issuer
        if self.mode is A2AVerifyMode.STRICT:
            missing = []
            if not jwks:
                missing.append("A2A_SUBAGENT_JWKS_URL")
            if not issuer:
                missing.append("A2A_SUBAGENT_ISSUER")
            if missing:
                raise ValueError(
                    f"A2A_VERIFY_MODE=strict requires {' and '.join(missing)} to be set"
                )
        elif self.mode is A2AVerifyMode.VERIFY:
            if bool(jwks) != bool(issuer):
                raise ValueError(
                    "A2A_VERIFY_MODE=verify requires both A2A_SUBAGENT_JWKS_URL and "
                    "A2A_SUBAGENT_ISSUER to be set or both to be empty"
                )

    @classmethod
    def from_env(cls) -> ReceiverSettings:
        """Read settings from ``A2A_*`` environment variables.

        Env mapping:

        ============================= ==================================
        Env var                       Field
        ============================= ==================================
        ``A2A_SUPERVISOR_URL``        ``supervisor_url``
        ``A2A_SUPERVISOR_API_KEY``    ``supervisor_api_key``
        ``A2A_CALLBACK_TOKEN_SECRET`` ``callback_token_secret``
        ``A2A_RECEIVER_URL``            ``receiver_url``
        ``A2A_SUBAGENT_ISSUER``       ``subagent_issuer``
        ``A2A_SUBAGENT_JWKS_URL``     ``subagent_jwks_url``
        ``A2A_SUBAGENT_URL``          ``subagent_url``
        ``A2A_SUBAGENT_API_KEY``      ``subagent_api_key``
        ``A2A_RECEIVER_PORT``           ``receiver_port``
        ``A2A_REDIS_URL``               ``redis_url``
        ``A2A_JTI_TTL_SECONDS``       ``jti_ttl_seconds``
        ``A2A_IAT_STALENESS_SECONDS`` ``iat_staleness_seconds``
        ``A2A_CALLBACK_TOKEN_TTL``    ``callback_token_ttl_seconds``
        ``A2A_VERIFY_MODE``                  ``mode``
        ============================= ==================================
        """
        settings = cls(
            supervisor_url=_env("SUPERVISOR_URL", "http://localhost:8000"),
            supervisor_api_key=_env("SUPERVISOR_API_KEY", ""),
            callback_token_secret=_env("CALLBACK_TOKEN_SECRET", ""),
            receiver_url=_env("RECEIVER_URL", "http://localhost:8001"),
            subagent_issuer=_env("SUBAGENT_ISSUER", ""),
            subagent_jwks_url=_env("SUBAGENT_JWKS_URL", ""),
            subagent_url=_env("SUBAGENT_URL", ""),
            subagent_api_key=_env("SUBAGENT_API_KEY", ""),
            receiver_port=_env_int("RECEIVER_PORT", 8001),
            redis_url=_env("REDIS_URL", ""),
            jti_ttl_seconds=_env_int("JTI_TTL_SECONDS", 900),
            iat_staleness_seconds=_env_int("IAT_STALENESS_SECONDS", 300),
            callback_token_ttl_seconds=_env_int("CALLBACK_TOKEN_TTL", 86400),
            mode=A2AVerifyMode(_env("VERIFY_MODE", "dev")),
        )
        settings.ensure_valid()
        if settings.mode is A2AVerifyMode.DEV and (
            settings.subagent_jwks_url or settings.subagent_issuer
        ):
            log.warning(
                "a2a_mode_dev_ignores_sender_auth_config",
                jwks_url=settings.subagent_jwks_url,
            )
        if (
            settings.mode is A2AVerifyMode.VERIFY
            and not settings.subagent_jwks_url
            and not settings.subagent_issuer
        ):
            log.warning(
                "a2a_mode_verify_without_sender_auth_config",
                reason="signed notifications will not be verified",
            )
        return settings


@dataclass(frozen=True)
class EmitterSettings:
    """Subagent-side emitter (push-notification signer) configuration.

    The *per-dispatch* push config (webhook ``url`` + opaque callback
    ``token``) is **not** here -- it arrives at runtime via the supervisor's
    ``PushNotificationConfig`` in ``config.configurable``.  This dataclass
    only carries the deployment-level signing material and retry policy.
    """

    private_key_pem: str = ""
    kid: str = "subagent"
    issuer: str = ""
    audience: str = ""
    jti_ttl_seconds: int = 300
    timeout_seconds: float = 30.0
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> EmitterSettings:
        """Read settings from ``A2A_*`` environment variables.

        ``private_key_pem`` may be supplied inline (``A2A_PRIVATE_KEY_PEM``)
        or as a path on disk (``A2A_PRIVATE_KEY_FILE``); the file wins.
        """
        pem = _env("PRIVATE_KEY_PEM", "")
        key_file = _env("PRIVATE_KEY_FILE", "")
        if key_file:
            pem = Path(key_file).read_text(encoding="utf-8")
        return cls(
            private_key_pem=pem,
            kid=_env("KID", "subagent"),
            issuer=_env("EMITTER_ISSUER", ""),
            audience=_env("EMITTER_AUDIENCE", ""),
            jti_ttl_seconds=_env_int("JTI_TTL_SECONDS", 300),
            timeout_seconds=_env_float("TIMEOUT_SECONDS", 30.0),
            max_retries=_env_int("MAX_RETRIES", 3),
            retry_backoff_seconds=_env_float("RETRY_BACKOFF_SECONDS", 1.0),
        )

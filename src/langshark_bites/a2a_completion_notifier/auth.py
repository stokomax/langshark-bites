"""Verify the A2A sender's JWT at the receiver webhook (JWKS / RS256).

Why this exists
---------------
The receiver terminates the A2A webhook POST.  It must confirm the
notification really comes from the subagent deployment before routing it
into the supervisor.  Per the A2A spec's asymmetric flow, the subagent
signs its JWT with a private key and publishes public keys at a JWKS
endpoint; the receiver fetches the key indicated by the ``kid`` header,
verifies the signature, and pins ``aud`` + ``iss``.

Design notes
------------
- ``JWKSClient`` caches fetched keys by ``kid`` and re-fetches only on an
  unknown ``kid`` (key rotation support without a long-lived cache).
- ``verify_sender_jwt`` raises typed ``SenderAuthError`` subclasses so the
  FastAPI layer can map each to a 401 without catching bare exceptions.
- The ``iat`` staleness window is checked separately from JWT ``exp``:
  PyJWT validates ``exp`` but not freshness, so a 24h ``exp`` with a
  10-minute-old ``iat`` must still be rejected.

Usage
-----
    from langshark_bites.a2a_completion_notifier.auth import (
        JWKSClient, verify_sender_jwt,
    )

    jwks = JWKSClient("https://subagents.example.com/.well-known/jwks.json")
    claims = await verify_sender_jwt(
        "Bearer eyJ...", jwks_client=jwks,
        audience=settings.receiver_url, issuer=settings.subagent_issuer,
    )
"""

from __future__ import annotations

import time
from typing import Any

import httpx

_FAST_TIMEOUT = httpx.Timeout(10.0)


class SenderAuthError(Exception):
    """Base class for sender-authentication failures (maps to HTTP 401)."""


class MissingBearerTokenError(SenderAuthError):
    """The Authorization header is absent or not a Bearer token."""


class UnknownKidError(SenderAuthError):
    """The JWT ``kid`` does not match any key in the JWKS set."""


class InvalidSignatureError(SenderAuthError):
    """The JWT failed signature/claim verification (PyJWT error surface)."""


class ExpiredTokenError(SenderAuthError):
    """The JWT ``exp`` has passed."""


class StaleTokenError(SenderAuthError):
    """The JWT ``iat`` is older than the configured staleness window."""


class MissingJtiError(SenderAuthError):
    """The JWT lacks the ``jti`` claim required for deduplication."""


class JWKSClient:
    """Fetches and caches JWKS public keys by ``kid``.

    Keys are cached after the first fetch.  When a previously-unseen
    ``kid`` appears (key rotation), the endpoint is re-queried; the new
    key is cached for the rest of the process lifetime.
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        """Fetch JWKS keys from ``jwks_url``, optionally over a supplied ``http`` client."""
        self._jwks_url = jwks_url
        self._http = http or httpx.AsyncClient()
        self._owned_http = http is None
        self._cache: dict[str, dict[str, Any]] = {}

    async def get_key(self, kid: str) -> dict[str, Any] | None:
        """Return the JWK for ``kid``, or None if not found."""
        if kid in self._cache:
            return self._cache[kid]

        response = await self._http.get(self._jwks_url, timeout=_FAST_TIMEOUT)
        response.raise_for_status()
        body = response.json()
        keys = body.get("keys", []) if isinstance(body, dict) else []
        for jwk in keys:
            if isinstance(jwk, dict) and jwk.get("kid") == kid:
                self._cache[kid] = jwk
                return jwk
        return None

    async def aclose(self) -> None:
        """Close the underlying HTTP client if this instance owns it."""
        if self._owned_http:
            await self._http.aclose()


async def verify_sender_jwt(
    authorization: str | None,
    *,
    jwks_client: JWKSClient,
    audience: str,
    issuer: str,
    iat_staleness_seconds: int = 300,
) -> dict[str, Any]:
    """Verify the A2A sender's JWT and return its validated claims.

    Args:
        authorization: The raw ``Authorization`` header value.
        jwks_client: Source of the subagent deployment's public keys.
        audience: The expected ``aud`` claim (the receiver's public URL).
        issuer: The expected ``iss`` claim (the subagent deployment).
        iat_staleness_seconds: Max age of the ``iat`` claim; older tokens
            are rejected as stale redeliveries.

    Returns:
        The validated JWT claims (contains ``jti``, ``taskId``, ``iss``,
        ``aud``, ``iat``, ``exp``).

    Raises:
        MissingBearerTokenError: No Bearer token in the header.
        UnknownKidError: No JWKS key matches the JWT's ``kid``.
        InvalidSignatureError: Signature or required-claim verification failed.
        ExpiredTokenError: The JWT ``exp`` has passed.
        StaleTokenError: The JWT ``iat`` is older than the staleness window.
        MissingJtiError: The JWT lacks the ``jti`` claim.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise MissingBearerTokenError("missing Bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    return await _verify_and_decode(
        token=token,
        jwks_client=jwks_client,
        audience=audience,
        issuer=issuer,
        iat_staleness_seconds=iat_staleness_seconds,
    )


async def _verify_and_decode(
    token: str,
    *,
    jwks_client: JWKSClient,
    audience: str,
    issuer: str,
    iat_staleness_seconds: int,
) -> dict[str, Any]:
    """Fetch the key by ``kid``, verify the signature + pinned claims."""
    import jwt

    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as exc:
        raise InvalidSignatureError("malformed JWT header") from exc
    if not kid:
        raise InvalidSignatureError("JWT header is missing kid")

    jwk = await jwks_client.get_key(kid)
    if jwk is None:
        raise UnknownKidError(f"no JWKS key for kid={kid!r}")

    try:
        key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
        # PyJWT's from_jwk return type includes RSAPrivateKey; verify only needs the public half.
        claims = jwt.decode(
            token,
            key,  # type: ignore[arg-type]
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ExpiredTokenError("sender JWT has expired") from exc
    except jwt.PyJWTError as exc:
        raise InvalidSignatureError("sender JWT verification failed") from exc

    iat = int(claims.get("iat", 0))
    if time.time() - iat > iat_staleness_seconds:
        raise StaleTokenError("sender JWT iat is too old")

    if not claims.get("jti"):
        raise MissingJtiError("sender JWT is missing the jti claim")

    return claims

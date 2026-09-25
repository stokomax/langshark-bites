"""A2A push notification JWT signing (RS256) and JWKS export.

Why this exists
---------------
The A2A streaming/async spec requires the *sending* server to authenticate
itself to the client's webhook with a signed JWT: claims ``iss``, ``aud``,
``iat``, ``exp``, ``jti``, ``taskId`` and a ``kid`` header, with public keys
published at a JWKS endpoint.  LangChain does not implement this push side,
so this module provides the signing half for the ``EmitterSettings``.

This is the subagent-deployment side.  The signing private key never leaves
the subagent deployment; ``jwks()`` produces the public key set for the
deployment's ``/.well-known/jwks.json``.

Usage
-----
    from langshark_bites.a2a_completion_notifier.signer import A2ASigner

    signer = A2ASigner(
        private_key_pem=pem, kid="subagent-1",
        issuer="https://subagents.example.com", audience="https://receiver.example.com",
    )
    jwt_token = signer.sign(task_id="task_123")
    jwks_doc = signer.jwks()   # serve this at your JWKS endpoint
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any


def _base64url_int(value: int) -> str:
    """Encode a non-negative integer as an RFC 7518 base64url string."""
    length = max(1, (value.bit_length() + 7) // 8)
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()


@dataclass(frozen=True)
class A2ASigner:
    """Signs A2A push notification JWTs for a subagent deployment.

    Attributes:
        private_key_pem: RSA private key (PEM, PKCS#8 or PKCS#1) used to
            sign outgoing notifications.
        kid: Key ID published in the JWT header and the JWKS set.  The
            receiver fetches the matching public key by this value.
        issuer: The ``iss`` claim -- the subagent deployment's URL.
        audience: The ``aud`` claim -- the receiver's webhook URL.  This
            must match ``RECEIVER_URL`` on the supervisor side.
        jti_ttl_seconds: Lifetime of each signed JWT (``exp`` - ``iat``).
    """

    private_key_pem: str
    kid: str
    issuer: str
    audience: str
    jti_ttl_seconds: int = 300

    def sign(self, task_id: str, *, now: int | None = None) -> str:
        """Sign a JWT authenticating the sender of one A2A notification.

        Args:
            task_id: The A2A task whose terminal event this JWT authenticates.
                Used for the ``taskId`` claim and to derive a unique ``jti``.
            now: Override the current time (tests).

        Returns:
            A signed RS256 JWT with a unique ``jti`` scoped to *task_id*.
        """
        now = int(now if now is not None else time.time())
        return _sign_with_headers(
            self.private_key_pem,
            self.kid,
            {
                "iss": self.issuer,
                "aud": self.audience,
                "iat": now,
                "exp": now + self.jti_ttl_seconds,
                "jti": f"{task_id}:{now}",
                "taskId": task_id,
            },
        )

    def jwks(self) -> dict[str, Any]:
        """Export the public key set for this signer.

        Returns:
            A JWKS document (``{"keys": [...]}``) containing this signer's
            RSA public key keyed by ``kid``.  Serve it at your deployment's
            JWKS endpoint (e.g. ``/.well-known/jwks.json``).
        """
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

        private_key = serialization.load_pem_private_key(
            self.private_key_pem.encode("ascii"), password=None
        )
        if not isinstance(private_key, RSAPrivateKey):
            raise TypeError(
                f"A2ASigner requires an RSA private key, got {type(private_key).__name__}"
            )
        public_numbers = private_key.public_key().public_numbers()
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": self.kid,
                    "use": "sig",
                    "alg": "RS256",
                    "n": _base64url_int(public_numbers.n),
                    "e": _base64url_int(public_numbers.e),
                }
            ]
        }


def _sign_with_headers(private_key_pem: str, kid: str, claims: dict[str, Any]) -> str:
    """Encode and sign a JWT with a ``kid`` header (deferred import)."""
    import jwt

    return jwt.encode(
        claims,
        private_key_pem,
        algorithm="RS256",
        headers={"kid": kid},
    )

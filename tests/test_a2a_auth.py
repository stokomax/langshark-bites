"""Tests for langshark_bites.a2a_completion_notifier.auth."""

from __future__ import annotations

import time

import httpx
import pytest

from langshark_bites.a2a_completion_notifier.auth import (
    ExpiredTokenError,
    InvalidSignatureError,
    JWKSClient,
    MissingBearerTokenError,
    MissingJtiError,
    StaleTokenError,
    UnknownKidError,
    verify_sender_jwt,
)

RECEIVER_URL = "https://receiver.example"
ISSUER = "https://subagents.example"


def _jwks_client(jwks_body: dict) -> JWKSClient:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=jwks_body))
    )
    return JWKSClient("https://subagents.example/.well-known/jwks.json", http=http)


async def _verify(token: str, *, jwks: JWKSClient, **kwargs) -> dict:
    return await verify_sender_jwt(
        f"Bearer {token}",
        jwks_client=jwks,
        audience=kwargs.get("audience", RECEIVER_URL),
        issuer=kwargs.get("issuer", ISSUER),
        iat_staleness_seconds=kwargs.get("staleness", 300),
    )


class TestVerifySuccess:
    async def test_valid_token_returns_claims(self, signer):
        token = signer.sign("task-1")
        jwks = _jwks_client(signer.jwks())
        claims = await _verify(token, jwks=jwks)
        assert claims["taskId"] == "task-1"
        assert claims["jti"]
        assert claims["iss"] == ISSUER
        assert claims["aud"] == RECEIVER_URL


class TestVerifyFailures:
    async def test_missing_bearer(self, signer):
        jwks = _jwks_client(signer.jwks())
        with pytest.raises(MissingBearerTokenError):
            await verify_sender_jwt(None, jwks_client=jwks, audience=RECEIVER_URL, issuer=ISSUER)
        with pytest.raises(MissingBearerTokenError):
            await verify_sender_jwt(
                "Basic abc", jwks_client=jwks, audience=RECEIVER_URL, issuer=ISSUER
            )

    async def test_unknown_kid(self, signer, rsa_pem):
        # A JWKS that does not contain the signer's kid.
        jwks_body = {"keys": [{"kid": "other-key", "kty": "RSA", "n": "", "e": ""}]}
        jwks = _jwks_client(jwks_body)
        token = signer.sign("task-x")
        with pytest.raises(UnknownKidError):
            await _verify(token, jwks=jwks)

    async def test_bad_signature_tampered_token(self, signer):
        jwks = _jwks_client(signer.jwks())
        token = signer.sign("task-x")
        tampered = token[:-5] + ("AAAAA" if not token.endswith("AAAAA") else "BBBBB")
        with pytest.raises(InvalidSignatureError):
            await _verify(tampered, jwks=jwks)

    async def test_wrong_audience_rejected(self, signer):
        jwks = _jwks_client(signer.jwks())
        token = signer.sign("task-x")
        with pytest.raises(InvalidSignatureError):
            await _verify(token, jwks=jwks, audience="https://evil.example")

    async def test_expired_token(self, signer):
        jwks = _jwks_client(signer.jwks())
        token = signer.sign("task-x", now=int(time.time()) - 3600)  # exp in the past
        with pytest.raises(ExpiredTokenError):
            await _verify(token, jwks=jwks)

    async def test_stale_iat_but_unexpired(self, rsa_pem):
        # Signer with a long TTL so the token is stale but NOT expired.
        from langshark_bites.a2a_completion_notifier.signer import A2ASigner

        long_signer = A2ASigner(
            private_key_pem=rsa_pem,
            kid="k1",
            issuer=ISSUER,
            audience=RECEIVER_URL,
            jti_ttl_seconds=86_400,
        )
        jwks = _jwks_client(long_signer.jwks())
        token = long_signer.sign("task-y", now=int(time.time()) - 3600)
        with pytest.raises(StaleTokenError):
            await _verify(token, jwks=jwks, staleness=300)

    async def test_missing_jti(self, signer, rsa_pem):
        # A validly-signed token without the jti claim.
        import jwt as pyjwt

        no_jti = pyjwt.encode(
            {
                "iss": ISSUER,
                "aud": RECEIVER_URL,
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
            },
            rsa_pem,
            algorithm="RS256",
            headers={"kid": signer.kid},
        )
        jwks = _jwks_client(signer.jwks())
        with pytest.raises(MissingJtiError):
            await _verify(no_jti, jwks=jwks)


class TestJWKSClient:
    async def test_caches_key_by_kid(self):
        import httpx as hx

        calls = {"n": 0}
        jwks_body = {"keys": [{"kid": "k1", "kty": "RSA", "n": "", "e": ""}]}

        def handler(request: hx.Request) -> hx.Response:
            calls["n"] += 1
            return hx.Response(200, json=jwks_body)

        http = hx.AsyncClient(transport=hx.MockTransport(handler))
        client = JWKSClient("https://x/.well-known/jwks.json", http=http)
        assert await client.get_key("k1") == jwks_body["keys"][0]
        assert await client.get_key("k1") == jwks_body["keys"][0]
        assert calls["n"] == 1

    async def test_unknown_kid_returns_none(self):
        jwks_body = {"keys": [{"kid": "k1", "kty": "RSA", "n": "", "e": ""}]}
        client = _jwks_client(jwks_body)
        assert await client.get_key("nope") is None

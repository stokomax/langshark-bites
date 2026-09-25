"""Tests for langshark_bites.a2a_completion_notifier.signer."""

from __future__ import annotations

import jwt
import pytest

from langshark_bites.a2a_completion_notifier.signer import A2ASigner

RECEIVER_URL = "https://receiver.example"
ISSUER = "https://subagents.example"


@pytest.fixture
def unit_signer(rsa_pem: str) -> A2ASigner:
    return A2ASigner(
        private_key_pem=rsa_pem,
        kid="k1",
        issuer=ISSUER,
        audience=RECEIVER_URL,
    )


class TestSign:
    def test_sign_returns_verifiable_rs256_jwt(self, unit_signer):
        token = unit_signer.sign("task_1", now=1_000_000)
        assert jwt.get_unverified_header(token)["kid"] == "k1"
        assert jwt.get_unverified_header(token)["alg"] == "RS256"

    def test_sign_claims_include_a2a_fields(self, unit_signer):
        token = unit_signer.sign("task_9", now=1_000_000)
        claims = jwt.decode(token, options={"verify_signature": False})
        assert claims["iss"] == ISSUER
        assert claims["aud"] == RECEIVER_URL
        assert claims["taskId"] == "task_9"
        assert claims["iat"] == 1_000_000
        assert claims["exp"] == 1_000_000 + 300
        assert claims["jti"].startswith("task_9:")

    def test_jti_scoped_to_task_and_time(self, unit_signer):
        a = unit_signer.sign("t", now=100)
        b = unit_signer.sign("t", now=101)
        c = unit_signer.sign("other", now=100)
        assert a != b != c
        assert (
            jwt.decode(a, options={"verify_signature": False})["jti"]
            != jwt.decode(b, options={"verify_signature": False})["jti"]
        )


class TestJWKS:
    def test_jwks_exports_matching_public_key(self, unit_signer, rsa_pem):
        jwks = unit_signer.jwks()
        assert len(jwks["keys"]) == 1
        key = jwks["keys"][0]
        assert key["kid"] == "k1"
        assert key["kty"] == "RSA"
        assert key["alg"] == "RS256"
        assert key["use"] == "sig"
        assert "n" in key and "e" in key

    def test_jwks_key_verifies_signed_token(self, unit_signer):
        import time

        token = unit_signer.sign("t1", now=int(time.time()))
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(unit_signer.jwks()["keys"][0])
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=RECEIVER_URL,
            issuer=ISSUER,
            options={"require": ["jti", "exp", "iat", "taskId"]},
        )
        assert claims["taskId"] == "t1"

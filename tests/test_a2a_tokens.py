"""Tests for langshark_bites.a2a_completion_notifier.tokens."""

from __future__ import annotations

import time

import pytest

from langshark_bites.a2a_completion_notifier.tokens import (
    CallbackTokenError,
    CallbackTokenExpiredError,
    CallbackTokenInvalidError,
    mint_callback_token,
    unseal_callback_token,
)


class TestMintUnsealRoundtrip:
    def test_roundtrip_returns_routing_fields(self):
        now = int(time.time())
        token = mint_callback_token("t1", "asst_1", "d1", "secret", now=now)
        parent = unseal_callback_token(token, "secret")
        assert parent.thread_id == "t1"
        assert parent.assistant_id == "asst_1"
        assert parent.dispatch_id == "d1"
        assert parent.exp == now + 86400

    def test_token_carries_default_ttl(self):
        now = int(time.time())
        token = mint_callback_token("t1", "asst_1", "d1", "secret", now=now)
        parent = unseal_callback_token(token, "secret")
        assert parent.exp == now + 86400

    def test_custom_ttl(self):
        now = int(time.time())
        token = mint_callback_token("t1", "asst_1", "d1", "secret", now=now, ttl_seconds=60)
        parent = unseal_callback_token(token, "secret")
        assert parent.exp == now + 60


class TestTokenErrors:
    def test_wrong_secret_raises_invalid(self):
        token = mint_callback_token("t1", "a1", "d1", "correct-key")
        with pytest.raises(CallbackTokenInvalidError):
            unseal_callback_token(token, "wrong-key")

    def test_expired_token_raises_expired(self):
        token = mint_callback_token(
            "t1",
            "a1",
            "d1",
            "secret",
            now=int(time.time()) - 86_400 - 10,  # minted >1 day ago
        )
        with pytest.raises(CallbackTokenExpiredError):
            unseal_callback_token(token, "secret")

    def test_garbage_token_raises_invalid(self):
        with pytest.raises(CallbackTokenInvalidError):
            unseal_callback_token("not-a-jwt", "secret")

    def test_missing_claims_raises_invalid(self):
        import jwt

        token = jwt.encode({"thread_id": "only"}, "secret", algorithm="HS256")
        with pytest.raises(CallbackTokenInvalidError):
            unseal_callback_token(token, "secret")

    def test_tampered_token_raises_invalid(self):
        token = mint_callback_token("t1", "a1", "d1", "secret")
        tampered = token[:-3] + ("xyz" if not token.endswith("xyz") else "abc")
        with pytest.raises(CallbackTokenError):
            unseal_callback_token(tampered, "secret")

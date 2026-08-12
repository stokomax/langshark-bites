"""Tests for langshark_bites.provider_failover."""

from __future__ import annotations

import pytest

from langshark_bites.provider_failover import (
    CreditExhaustedCallback,
    ExhaustedProviderError,
    _exhausted,
    create_model_with_fallback,
    is_fallback_error,
)


@pytest.fixture(autouse=True)
def _reset_exhausted():
    """Reset the exhausted-provider registry between tests."""
    _exhausted._providers.clear()
    yield
    _exhausted._providers.clear()


class TestExhaustedProviders:
    def test_add_and_check(self):
        assert not _exhausted.exhausted("claude")
        _exhausted.add("claude")
        assert _exhausted.exhausted("claude")

    def test_all_snapshot(self):
        _exhausted.add("claude")
        _exhausted.add("deepseek")
        assert _exhausted.all == frozenset({"claude", "deepseek"})

    def test_on_exhausted_callback(self):
        fired: list[str] = []
        _exhausted.on_exhausted(lambda p: fired.append(p))
        _exhausted.add("openai")
        assert fired == ["openai"]


class TestExhaustedProviderError:
    def test_message(self):
        err = ExhaustedProviderError("claude", "claude-sonnet-5")
        assert err.provider == "claude"
        assert err.model_name == "claude-sonnet-5"
        assert "claude" in str(err)


class TestCreditExhaustedCallback:
    def test_detects_credit_error(self):
        cb = CreditExhaustedCallback("claude-sonnet-5")
        cb.on_llm_error(Exception("credit balance is too low"))
        assert _exhausted.exhausted("claude")

    def test_ignores_unrelated_error(self):
        cb = CreditExhaustedCallback("claude-sonnet-5")
        cb.on_llm_error(Exception("some other error"))
        assert not _exhausted.exhausted("claude")

    def test_extra_patterns(self):
        cb = CreditExhaustedCallback("claude-sonnet-5", extra_patterns=["custom pattern"])
        cb.on_llm_error(Exception("custom pattern triggered"))
        assert _exhausted.exhausted("claude")


class TestIsFallbackError:
    def test_credit_message(self):
        assert is_fallback_error(Exception("insufficient balance"))

    def test_rate_limit_not_fallback(self):
        assert not is_fallback_error(Exception("rate limit exceeded 429"))

    def test_unknown_error_not_fallback(self):
        assert not is_fallback_error(Exception("random failure"))


class TestCreateModelWithFallback:
    def test_requires_model_builder(self):
        with pytest.raises(ValueError, match="model_builder is required"):
            create_model_with_fallback("claude-sonnet-5", "deepseek-chat")

    def test_no_fallbacks_returns_plain_model(self):
        requested: list[str] = []

        def builder(name: str, max_tokens: int = 8192):
            requested.append(name)
            return object()

        result = create_model_with_fallback(
            "claude-sonnet-5", "", model_builder=builder
        )
        assert requested == ["claude-sonnet-5"]
        # No fallbacks → plain model returned (not a RunnableWithFallbacks)
        assert not hasattr(result, "with_fallbacks")

    def test_skips_exhausted_primary(self):
        _exhausted.add("claude")
        requested: list[str] = []

        def builder(name: str, max_tokens: int = 8192):
            requested.append(name)
            return object()

        create_model_with_fallback(
            "claude-sonnet-5", "deepseek-chat", model_builder=builder
        )
        # Primary skipped, fallback promoted
        assert requested == ["deepseek-chat"]

    def test_skips_exhausted_fallback(self):
        _exhausted.add("deepseek")
        requested: list[str] = []

        def builder(name: str, max_tokens: int = 8192):
            requested.append(name)
            return object()

        result = create_model_with_fallback(
            "claude-sonnet-5", "deepseek-chat", model_builder=builder
        )
        # Exhausted fallback dropped → only primary built, plain model returned
        assert requested == ["claude-sonnet-5"]
        assert not hasattr(result, "with_fallbacks")

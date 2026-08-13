"""Tests for langshark_bites.provider_failover."""

from __future__ import annotations

import pytest

from langshark_bites.provider_failover import (
    ExhaustedProviderCallback,
    ExhaustedProviderError,
    _exhausted,  # internal, test-reset only
    create_model_with_fallback,
    is_fallback_error,
    is_provider_exhausted,
    mark_provider_exhausted,
    model_with_fallbacks,
    on_provider_exhausted,
)


@pytest.fixture(autouse=True)
def _reset_exhausted():
    """Reset the exhausted-provider registry between tests."""
    _exhausted._providers.clear()
    yield
    _exhausted._providers.clear()


class TestExhaustedProviders:
    def test_mark_and_check(self):
        assert not is_provider_exhausted("claude")
        mark_provider_exhausted("claude")
        assert is_provider_exhausted("claude")

    def test_all_snapshot(self):
        mark_provider_exhausted("claude")
        mark_provider_exhausted("deepseek")
        assert _exhausted.all == frozenset({"claude", "deepseek"})

    def test_on_provider_exhausted_callback(self):
        fired: list[str] = []
        on_provider_exhausted(lambda p: fired.append(p))
        mark_provider_exhausted("openai")
        assert fired == ["openai"]


class TestExhaustedProviderError:
    def test_message(self):
        err = ExhaustedProviderError("claude", "claude-sonnet-5")
        assert err.provider == "claude"
        assert err.model_name == "claude-sonnet-5"
        assert "claude" in str(err)


class TestExhaustedProviderCallback:
    def test_detects_credit_error(self):
        cb = ExhaustedProviderCallback("claude-sonnet-5")
        cb.on_llm_error(Exception("credit balance is too low"))
        assert is_provider_exhausted("claude")

    def test_ignores_unrelated_error(self):
        cb = ExhaustedProviderCallback("claude-sonnet-5")
        cb.on_llm_error(Exception("some other error"))
        assert not is_provider_exhausted("claude")

    def test_extra_patterns(self):
        cb = ExhaustedProviderCallback("claude-sonnet-5", extra_patterns=["custom pattern"])
        cb.on_llm_error(Exception("custom pattern triggered"))
        assert is_provider_exhausted("claude")


class TestIsFallbackError:
    def test_credit_message(self):
        assert is_fallback_error(Exception("insufficient balance"))

    def test_rate_limit_not_fallback(self):
        assert not is_fallback_error(Exception("rate limit exceeded 429"))

    def test_unknown_error_not_fallback(self):
        assert not is_fallback_error(Exception("random failure"))


class TestModelWithFallbacks:
    def test_requires_model_builder(self):
        with pytest.raises(ValueError, match="model_builder is required"):
            model_with_fallbacks("claude-sonnet-5", "deepseek-chat")

    def test_no_fallbacks_returns_plain_model(self):
        requested: list[str] = []

        def builder(name: str, max_tokens: int = 8192):
            requested.append(name)
            return object()

        result = model_with_fallbacks("claude-sonnet-5", "", model_builder=builder)
        assert requested == ["claude-sonnet-5"]
        # No fallbacks → plain model returned (not a RunnableWithFallbacks)
        assert not hasattr(result, "with_fallbacks")

    def test_skips_exhausted_primary(self):
        mark_provider_exhausted("claude")
        requested: list[str] = []

        def builder(name: str, max_tokens: int = 8192):
            requested.append(name)
            return object()

        model_with_fallbacks("claude-sonnet-5", "deepseek-chat", model_builder=builder)
        # Primary skipped, fallback promoted
        assert requested == ["deepseek-chat"]

    def test_skips_exhausted_fallback(self):
        mark_provider_exhausted("deepseek")
        requested: list[str] = []

        def builder(name: str, max_tokens: int = 8192):
            requested.append(name)
            return object()

        result = model_with_fallbacks(
            "claude-sonnet-5", "deepseek-chat", model_builder=builder
        )
        # Exhausted fallback dropped → only primary built, plain model returned
        assert requested == ["claude-sonnet-5"]
        assert not hasattr(result, "with_fallbacks")

    def test_create_model_with_fallback_alias(self):
        """The old name remains as a backwards-compatible alias."""
        requested: list[str] = []

        def builder(name: str, max_tokens: int = 8192):
            requested.append(name)
            return object()

        # Both names must reference the same callable.
        assert create_model_with_fallback is model_with_fallbacks

        # No fallbacks → plain model path, works via the alias too.
        result = create_model_with_fallback(
            "claude-sonnet-5", "", model_builder=builder
        )
        assert requested == ["claude-sonnet-5"]
        assert not hasattr(result, "with_fallbacks")

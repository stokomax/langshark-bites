"""Runnable example for the provider_failover bite.

This example shows how to build a model factory, guard it against exhausted
providers, and use model_with_fallbacks to get a model with a baked-in
fallback chain. It uses a stand-in model factory so it runs without real
LLM provider packages or API keys.

Run with:

    uv run python examples/provider_failover.py

See the docs: https://stokomax.github.io/langshark-bites/provider_failover/
"""

from __future__ import annotations

from langshark_bites.provider_failover import (
    ExhaustedProviderError,
    is_provider_exhausted,
    mark_provider_exhausted,
    model_with_fallbacks,
)


class FakeModel:
    """Stand-in for a LangChain chat model."""

    def __init__(self, name: str):
        self.name = name

    def with_fallbacks(self, fallbacks, exceptions_to_handle=None):
        """Stand-in for RunnableWithFallbacks. Returns a wrapper object."""
        return FallbackChain(self, fallbacks)

    def __repr__(self) -> str:
        return f"FakeModel({self.name})"


class FallbackChain:
    """Stand-in for the object returned by with_fallbacks()."""

    def __init__(self, primary, fallbacks):
        self.primary = primary
        self.fallbacks = fallbacks

    def __repr__(self) -> str:
        return f"FallbackChain(primary={self.primary}, fallbacks={self.fallbacks})"


def create_model(model_name: str, max_tokens: int = 8192) -> FakeModel:
    """Model factory. Guards against exhausted providers, like a real one."""
    if is_provider_exhausted(model_name):
        raise ExhaustedProviderError(model_name, model_name)
    return FakeModel(model_name)


def main() -> None:
    # Normal case: primary plus two fallbacks.
    model = model_with_fallbacks(
        "claude-sonnet-4-5",
        "deepseek-v4-flash,gpt-4o-mini",
        max_tokens=8192,
        model_builder=create_model,
    )
    print("normal:", model)

    # Simulate the primary provider's credit running out.
    mark_provider_exhausted("claude")
    print("claude marked exhausted")

    # Now the primary is skipped and the first fallback is promoted.
    model = model_with_fallbacks(
        "claude-sonnet-4-5",
        "deepseek-v4-flash,gpt-4o-mini",
        max_tokens=8192,
        model_builder=create_model,
    )
    print("after exhaustion:", model)

    # A direct create_model call for the exhausted provider raises.
    try:
        create_model("claude-sonnet-4-5")
    except ExhaustedProviderError as exc:
        print("guarded call raised:", exc)


if __name__ == "__main__":
    main()

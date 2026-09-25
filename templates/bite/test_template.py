"""Tests for langshark_bites.<bite_name>.<public_func>.

Verifies the documented public API. Side effects are mocked so the tests are
fast and deterministic (follow tests/test_api_backoff.py as the reference).
"""

from __future__ import annotations

import pytest

from langshark_bites.<bite_name> import <public_func>


@pytest.mark.asyncio
async def test_<public_func>_happy_path():
    """The documented happy path returns the expected value."""
    assert await <public_func>("x") == "x"


@pytest.mark.asyncio
async def test_<public_func>_edge_case():
    """Edge case from the module docstring is handled."""
    # ...assert documented edge behavior...
    pass


def test_<public_func>_sync_behavior():
    """Synchronous behavior (if any) is covered without an async mark."""
    assert <public_func>("x") == "x"
"""Package-level metadata tests: wheel packaging, PEP 561 marker, version.

These guard the wheel-build decisions: the typed marker lives at the package
root (PEP 561), ``__version__`` mirrors the installed distribution, and the
public API stays alphabetically sorted per repo convention.
"""

from __future__ import annotations

import importlib.metadata
import pathlib

import langshark_bites

PACKAGE_DIR = pathlib.Path(langshark_bites.__file__).resolve().parent


def test_py_typed_marker_at_package_root() -> None:
    """The wheel ships a PEP 561 typed marker at the package root."""
    assert (PACKAGE_DIR / "py.typed").is_file()


def test_version_matches_installed_metadata() -> None:
    """__version__ mirrors the installed distribution's version."""
    assert langshark_bites.__version__ == importlib.metadata.version("langshark-bites")


def test_public_api_all_is_sorted() -> None:
    """__all__ is maintained alphabetically, per repo convention (case-folded)."""
    assert langshark_bites.__all__ == sorted(langshark_bites.__all__, key=str.casefold)

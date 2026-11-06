"""Pinning coverage for a foundation decision that was previously defended by prose alone.

The test here fails if one specific, deliberate decision is reverted, so the site that took the decision can point at
a test instead of arguing for itself (``bzh:comment-locality``, ``bzh:mutation-review-selection``)."""

from __future__ import annotations

import importlib
import importlib.metadata

import pytest

import blizzard


def test_version_tracks_the_installed_distribution_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """``blizzard.__version__`` is whatever the *installed* distribution's metadata says —
    a build-stamped wheel reports its own version, not a literal frozen into the source."""

    def stamped_version(_distribution: str) -> str:
        return "9.9.9.dev999"

    monkeypatch.setattr(importlib.metadata, "version", stamped_version)
    try:
        reloaded = importlib.reload(blizzard)
        assert reloaded.__version__ == "9.9.9.dev999"
    finally:
        monkeypatch.undo()
        importlib.reload(blizzard)
    assert blizzard.__version__ == importlib.metadata.version("blizzard")

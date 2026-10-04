"""The harness package's public face for version admission and offline classification —
delegating to ``harness/internal/`` (``bzh:internal-visibility``)."""

from __future__ import annotations

from pathlib import Path

from packaging.specifiers import SpecifierSet

from blizzard.runner.harness import harness_shared, offline_compatibility
from blizzard.runner.harness.compatibility import CompatibilityClassification


def version_admitted(version: str, admitted_range: SpecifierSet) -> bool:
    return harness_shared.version_admitted(version, admitted_range)


def classify_offline(
    harness_id: str,
    observed_version: str | None,
    admitted_range: SpecifierSet,
    *,
    corpus_root: Path = offline_compatibility.DEFAULT_CORPUS_ROOT,
) -> CompatibilityClassification | None:
    return offline_compatibility.classify_offline(harness_id, observed_version, admitted_range, corpus_root=corpus_root)


__all__ = ["classify_offline", "version_admitted"]

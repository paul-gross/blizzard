"""Offline compatibility classification against the committed fixture corpus (blizzard#438).

Distinct from :class:`~blizzard.runner.harness.compatibility.CompatibilityDiagnostic`, which
runs a live probe: this classifies an already-observed version by looking up its committed
``contracts/<harness_id>/<version>/manifest.json`` rather than exercising a binary or a
provider. Read-only and dependency-free beyond the filesystem read itself."""

from __future__ import annotations

import json
from pathlib import Path

from blizzard.runner.harness.compatibility import CompatibilityClassification

# Package-relative, not repo-root-relative: the wheel ships only `src/blizzard`
# (`pyproject.toml`'s `packages`), so the corpus lives under `harness/contracts` and is
# found the same way in a checkout and an installed wheel alike.
DEFAULT_CORPUS_ROOT = Path(__file__).resolve().parent.parent / "contracts"


def classify_offline(
    harness_id: str,
    observed_version: str | None,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
) -> CompatibilityClassification | None:
    """The pinned classification a committed corpus fixture records for ``observed_version``,
    or ``None`` when no version was observed at all, or no fixture manifest proves it — the
    "unknown version" case a caller's evaluator treats identically either way. ``corpus_root``
    defaults to this repo's own ``contracts/`` tree but stays injectable for testability."""

    if observed_version is None:
        return None
    manifest_path = corpus_root / harness_id / observed_version / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict):
        return None
    live_evidence = manifest.get("live_evidence")
    if not isinstance(live_evidence, dict):
        return None
    label = live_evidence.get("classification")
    if not isinstance(label, str):
        return None
    try:
        return CompatibilityClassification(label)
    except ValueError:
        return None


__all__ = ["DEFAULT_CORPUS_ROOT", "classify_offline"]

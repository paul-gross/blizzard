"""Offline compatibility classification against the committed fixture corpus (blizzard#438).

Distinct from :class:`~blizzard.runner.harness.compatibility.CompatibilityDiagnostic`, which
runs a live probe: this classifies an already-observed version by looking up its committed
``contracts/<harness_id>/<version>/manifest.json`` rather than exercising a binary or a
provider. Read-only and dependency-free beyond the filesystem read itself."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from blizzard.runner.harness.compatibility import CompatibilityClassification

# Package-relative, not repo-root-relative: the wheel ships only `src/blizzard`
# (`pyproject.toml`'s `packages`), so the corpus lives under `harness/contracts` and is
# found the same way in a checkout and an installed wheel alike.
DEFAULT_CORPUS_ROOT = Path(__file__).resolve().parent.parent / "contracts"


class CorpusConfigurationError(RuntimeError):
    """A binding declared an admitted version with no committed corpus manifest under
    ``corpus_root`` — a configuration error raised at construction/import time, never a
    silently-unclassifiable runtime case. The two-part admitted-version claim (declared
    *and* backed by a corpus fixture) must land together."""


def assert_admitted_versions_have_corpus(
    harness_id: str,
    admitted_versions: Iterable[str],
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
) -> None:
    """Raise :class:`CorpusConfigurationError` naming every ``admitted_versions`` member
    with no ``corpus_root/harness_id/<version>/manifest.json`` — call this once, at module
    import or binding-construction time, right after a binding declares its admitted set."""

    missing = sorted(
        version for version in admitted_versions if not (corpus_root / harness_id / version / "manifest.json").is_file()
    )
    if missing:
        raise CorpusConfigurationError(
            f"{harness_id!r} declares admitted version(s) with no committed corpus manifest: {', '.join(missing)}"
        )


def classify_offline(
    harness_id: str,
    observed_version: str | None,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    admitted_versions: frozenset[str] | None = None,
) -> CompatibilityClassification | None:
    """The pinned classification a committed corpus fixture records for ``observed_version``,
    or ``None`` when no version was observed, no fixture manifest proves it, or
    ``admitted_versions`` is supplied and excludes it — the same "unknown" outcome either way,
    so a stray directory for a version this binding no longer admits can never resolve a
    classification. ``corpus_root`` defaults to this repo's own ``contracts/`` tree."""

    if observed_version is None:
        return None
    if admitted_versions is not None and observed_version not in admitted_versions:
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


__all__ = [
    "DEFAULT_CORPUS_ROOT",
    "CorpusConfigurationError",
    "assert_admitted_versions_have_corpus",
    "classify_offline",
]

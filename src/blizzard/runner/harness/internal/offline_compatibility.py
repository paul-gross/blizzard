"""Offline compatibility classification against the committed fixture corpus (blizzard#438).
Distinct from :class:`~blizzard.runner.harness.compatibility.CompatibilityDiagnostic`, which
runs a live probe: this classifies an already-observed version from its committed
``contracts/<harness_id>/<version>/manifest.json``, read-only and dependency-free beyond
the filesystem read itself. Corpus fixtures stay pinned per exact version: an observed
version resolves to the newest committed corpus at or below it, inside the admitted range."""

from __future__ import annotations

import json
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from blizzard.runner.harness.compatibility import CompatibilityClassification
from blizzard.runner.harness.internal import harness_shared

# Package-relative, not repo-root-relative: the wheel ships only `src/blizzard`
# (`pyproject.toml`'s `packages`), so the corpus lives under `harness/contracts` and is
# found the same way in a checkout and an installed wheel alike.
DEFAULT_CORPUS_ROOT = Path(__file__).resolve().parent.parent / "contracts"


class CorpusConfigurationError(RuntimeError):
    """A binding declared an admitted range with no committed corpus manifest inside it under
    ``corpus_root`` — the two-part admitted-range claim (declared *and* backed by at least one
    corpus fixture) must land together. Signals the defect only: its one caller
    (``OpenCodeHealthProbe.__init__``) catches and logs it rather than raising further."""


def admitted_corpus_versions(
    harness_id: str,
    admitted_range: SpecifierSet,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
) -> tuple[str, ...]:
    """Every committed ``corpus_root/harness_id/<version>/manifest.json`` version whose version
    lies inside ``admitted_range``, oldest first — the corpus fixtures a reference-corpus lookup
    or a binding's own declared-degradations union may ever resolve to. Membership is checked
    through :func:`~blizzard.runner.harness.internal.harness_shared.version_admitted`
    (blizzard#604), the one rule a corpus name and an observed version are both judged by."""

    harness_dir = corpus_root / harness_id
    if not harness_dir.is_dir():
        return ()
    versions: list[tuple[Version, str]] = []
    for child in harness_dir.iterdir():
        if not child.is_dir() or not (child / "manifest.json").is_file():
            continue
        if not harness_shared.version_admitted(child.name, admitted_range):
            continue
        versions.append((Version(child.name), child.name))
    versions.sort(key=lambda entry: entry[0])
    return tuple(name for _, name in versions)


def assert_admitted_range_has_corpus(
    harness_id: str,
    admitted_range: SpecifierSet,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
) -> None:
    """Raise :class:`CorpusConfigurationError` when no committed corpus manifest lies inside
    ``admitted_range`` at all — call this once, at module import or binding-construction time,
    right after a binding declares its admitted range."""

    if not admitted_corpus_versions(harness_id, admitted_range, corpus_root=corpus_root):
        range_repr = repr(str(admitted_range))
        raise CorpusConfigurationError(
            f"{harness_id!r} declares admitted range {range_repr} with no committed corpus manifest inside it"
        )


def reference_corpus_version(
    harness_id: str,
    observed_version: str,
    admitted_range: SpecifierSet,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
) -> str | None:
    """The committed corpus version that stands in for ``observed_version``: the newest
    :func:`admitted_corpus_versions` member at or below it. ``None`` when ``observed_version``
    doesn't parse, or no committed corpus qualifies — never a corpus *above* what was observed,
    since that would classify a version against evidence captured from a later one."""

    try:
        observed = Version(observed_version)
    except InvalidVersion:
        return None
    candidates = [
        version
        for version in admitted_corpus_versions(harness_id, admitted_range, corpus_root=corpus_root)
        if Version(version) <= observed
    ]
    return candidates[-1] if candidates else None


def classify_offline(
    harness_id: str,
    observed_version: str | None,
    admitted_range: SpecifierSet,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
) -> CompatibilityClassification | None:
    """The pinned classification a committed corpus fixture records for ``observed_version``'s
    own :func:`reference_corpus_version`, or ``None`` when no version was observed or none
    resolves. ``admitted_range`` only selects the reference corpus — never asserts
    ``observed_version`` is itself admitted (D2); a caller checks that separately.
    ``corpus_root`` defaults to this repo's own ``contracts/`` tree."""

    if observed_version is None:
        return None
    reference = reference_corpus_version(harness_id, observed_version, admitted_range, corpus_root=corpus_root)
    if reference is None:
        return None
    manifest_path = corpus_root / harness_id / reference / "manifest.json"
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
    "admitted_corpus_versions",
    "assert_admitted_range_has_corpus",
    "classify_offline",
    "reference_corpus_version",
]

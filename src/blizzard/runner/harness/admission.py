"""Harness version admission — pure rules over already-observed values.

Whether an observed version is a member of a binding's admitted range, the tri-state verdict the
health evaluator reads, which committed reference corpus stands in for an observed version, and
which classification a corpus manifest records. No filesystem, subprocess, or clock read happens
here: :mod:`.offline_compatibility` reads them and hands the values in."""

from __future__ import annotations

import re
from collections.abc import Iterable

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from blizzard.runner.harness.compatibility import CompatibilityClassification

# A semver pre-release: `X.Y.Z-<identifiers>`, which `Version` would otherwise parse by PEP 440's rules instead.
_SEMVER_PRERELEASE_PATTERN = re.compile(r"^\d+\.\d+\.\d+-")


def version_admitted(version: str, admitted_range: SpecifierSet) -> bool:
    """Whether ``version`` is a member of ``admitted_range`` — the one membership check a
    binding's declared range and any caller's own version, a corpus directory name included,
    are ever compared through. An unparsable or semver-pre-release ``version``
    reads as not admitted rather than raising. Pre-releases are always excluded
    (``prereleases=False``), never ``SpecifierSet``'s own no-other-candidate ``filter`` default."""
    if _SEMVER_PRERELEASE_PATTERN.match(version):
        return False
    try:
        parsed = Version(version)
    except InvalidVersion:
        return False
    return admitted_range.contains(parsed, prereleases=False)


def admission_verdict(normalized_version: str | None, admitted_range: SpecifierSet | None) -> bool | None:
    """The health evaluator's version-membership evidence: ``None`` when no version was
    observed (distinct from a non-member), ``False`` when the binding declares no range to be a
    member of, else :func:`version_admitted`."""
    if normalized_version is None:
        return None
    return admitted_range is not None and version_admitted(normalized_version, admitted_range)


def select_reference_corpus(
    observed_version: str, corpus_versions: Iterable[str], admitted_range: SpecifierSet
) -> str | None:
    """The committed corpus version that stands in for ``observed_version``: the newest of
    ``corpus_versions`` inside ``admitted_range`` at or below it. ``None`` when
    ``observed_version`` doesn't parse, or no corpus qualifies — never a corpus *above* what was
    observed, since that would classify a version against evidence captured from a later one."""
    try:
        observed = Version(observed_version)
    except InvalidVersion:
        return None
    candidates = sorted(
        (Version(version), version)
        for version in corpus_versions
        if version_admitted(version, admitted_range) and Version(version) <= observed
    )
    return candidates[-1][1] if candidates else None


def classification_of_manifest(manifest: object) -> CompatibilityClassification | None:
    """The pinned classification a parsed corpus manifest records under
    ``live_evidence.classification``, or ``None`` when the manifest is not that shape or names
    no known classification."""
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


__all__ = ["admission_verdict", "classification_of_manifest", "select_reference_corpus", "version_admitted"]

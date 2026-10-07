"""Offline compatibility classification against the committed fixture corpus.
Distinct from :class:`~blizzard.runner.harness.compatibility.CompatibilityDiagnostic`, which
runs a live probe: this classifies an already-observed version from its committed
``contracts/<harness_id>/<version>/manifest.json``, read through the :class:`ICompatibilityCorpus`
port (driver: ``internal/committed_corpus.py``). An observed version resolves to the newest
committed corpus at or below it, inside the admitted range."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from blizzard.runner.harness.admission import classification_of_manifest, select_reference_corpus, version_admitted
from blizzard.runner.harness.compatibility import CompatibilityClassification


class ICompatibilityCorpus(Protocol):
    """The committed fixture corpus: which versions it holds per harness, and each one's manifest."""

    def versions(self, harness_id: str) -> tuple[str, ...]:
        """Every version ``harness_id`` has a committed manifest for, in no particular order."""
        ...

    def manifest(self, harness_id: str, version: str) -> Mapping[str, object] | None:
        """That version's manifest, or ``None`` when it is absent or unreadable."""
        ...


class CorpusConfigurationError(RuntimeError):
    """A binding declared an admitted range with no committed corpus manifest inside it — an
    admitted range must be backed by at least one corpus fixture."""


def admitted_corpus_versions(
    corpus: ICompatibilityCorpus, harness_id: str, admitted_range: SpecifierSet
) -> tuple[str, ...]:
    """Every committed ``harness_id`` corpus version that lies inside ``admitted_range``, oldest
    first — the corpus fixtures a reference-corpus lookup or a binding's own declared-degradations
    union may ever resolve to. Membership is checked through
    :func:`~blizzard.runner.harness.admission.version_admitted`,
    the one rule a corpus name and an observed version are both judged by."""

    admitted = [(Version(name), name) for name in corpus.versions(harness_id) if version_admitted(name, admitted_range)]
    admitted.sort(key=lambda entry: entry[0])
    return tuple(name for _, name in admitted)


def assert_admitted_range_has_corpus(
    corpus: ICompatibilityCorpus, harness_id: str, admitted_range: SpecifierSet
) -> None:
    """Raise :class:`CorpusConfigurationError` when no committed corpus manifest lies inside
    ``admitted_range`` at all — call this once, at binding-construction time,
    right after a binding declares its admitted range."""

    if not admitted_corpus_versions(corpus, harness_id, admitted_range):
        range_repr = repr(str(admitted_range))
        raise CorpusConfigurationError(
            f"{harness_id!r} declares admitted range {range_repr} with no committed corpus manifest inside it"
        )


def reference_corpus_version(
    corpus: ICompatibilityCorpus, harness_id: str, observed_version: str, admitted_range: SpecifierSet
) -> str | None:
    """The committed corpus version that stands in for ``observed_version`` — the listing of
    :func:`admitted_corpus_versions`, judged by
    :func:`~blizzard.runner.harness.admission.select_reference_corpus`."""

    return select_reference_corpus(
        observed_version, admitted_corpus_versions(corpus, harness_id, admitted_range), admitted_range
    )


def classify_offline(
    corpus: ICompatibilityCorpus, harness_id: str, observed_version: str | None, admitted_range: SpecifierSet
) -> CompatibilityClassification | None:
    """The pinned classification a committed corpus fixture records for ``observed_version``'s
    own :func:`reference_corpus_version`, or ``None`` when no version was observed or none
    resolves. ``admitted_range`` only selects the reference corpus — never asserts
    ``observed_version`` is itself admitted; a caller checks that separately."""

    if observed_version is None:
        return None
    reference = reference_corpus_version(corpus, harness_id, observed_version, admitted_range)
    if reference is None:
        return None
    return classification_of_manifest(corpus.manifest(harness_id, reference))


__all__ = [
    "CorpusConfigurationError",
    "ICompatibilityCorpus",
    "admitted_corpus_versions",
    "assert_admitted_range_has_corpus",
    "classify_offline",
    "reference_corpus_version",
]

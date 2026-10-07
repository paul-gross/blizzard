"""``classify_offline``'s corpus lookup — reads the committed fixture corpus
rather than running a live probe.

``classify_offline`` classifies a version by resolving it to a *reference corpus* — the
newest committed corpus at or below it, inside the admitted range — never by requiring a
corpus for the exact observed version. The admitted range only selects the reference
corpus; whether the observed version is itself admitted is the caller's question, not
this module's."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

from blizzard.runner.harness.compatibility import CompatibilityClassification
from blizzard.runner.harness.internal.committed_corpus import DEFAULT_CORPUS_ROOT, CommittedCorpus
from blizzard.runner.harness.offline_compatibility import (
    CorpusConfigurationError,
    ICompatibilityCorpus,
    admitted_corpus_versions,
    assert_admitted_range_has_corpus,
    classify_offline,
    reference_corpus_version,
)
from blizzard.runner.harness.opencode.compatibility.probe import ADMITTED_OPENCODE_RANGE, PINNED_OPENCODE_VERSION
from blizzard.runner.harness.opencode.version import normalize_opencode_version
from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_PACKAGE_ROOT = repo_root() / "src" / "blizzard" / "runner" / "harness"
# Keyed off the admitted range's own committed corpus, not a hardcoded
# literal — there is exactly one committed corpus today, but this stays correct once a
# second one lands.
_AN_ADMITTED_OPENCODE_VERSION = admitted_corpus_versions(CommittedCorpus(), "opencode", ADMITTED_OPENCODE_RANGE)[0]
_CORPUS_DIR = _PACKAGE_ROOT / "contracts" / "opencode" / _AN_ADMITTED_OPENCODE_VERSION


def test_default_corpus_root_is_the_harness_packages_own_contracts_tree() -> None:
    assert DEFAULT_CORPUS_ROOT == _PACKAGE_ROOT / "contracts"


def test_the_pinned_opencode_corpus_classifies_degraded() -> None:
    """`contracts/opencode/1.18.25/manifest.json`'s own `live_evidence.classification`
    is `"degraded"` — pinned here so a corpus edit that silently changes it is caught."""
    assert (
        classify_offline(CommittedCorpus(), "opencode", _AN_ADMITTED_OPENCODE_VERSION, ADMITTED_OPENCODE_RANGE)
        is CompatibilityClassification.DEGRADED
    )


def test_no_observed_version_is_unknown() -> None:
    assert classify_offline(CommittedCorpus(), "opencode", None, ADMITTED_OPENCODE_RANGE) is None


def test_a_version_above_every_committed_corpus_still_resolves_to_the_newest_one() -> None:
    """1.18.31 is inside `ADMITTED_OPENCODE_RANGE` and above the only committed corpus
    (1.18.25); it resolves to that corpus as its reference rather than reading as unknown."""
    assert (
        reference_corpus_version(CommittedCorpus(), "opencode", "1.18.31", ADMITTED_OPENCODE_RANGE)
        == _AN_ADMITTED_OPENCODE_VERSION
    )
    assert (
        classify_offline(CommittedCorpus(), "opencode", "1.18.31", ADMITTED_OPENCODE_RANGE)
        is CompatibilityClassification.DEGRADED
    )


def test_an_unparsable_observed_version_is_unknown_rather_than_raising() -> None:
    assert (
        reference_corpus_version(CommittedCorpus(), "opencode", "9.9.9-does-not-exist", ADMITTED_OPENCODE_RANGE) is None
    )
    assert classify_offline(CommittedCorpus(), "opencode", "9.9.9-does-not-exist", ADMITTED_OPENCODE_RANGE) is None


def test_an_unknown_harness_id_is_unknown() -> None:
    assert (
        classify_offline(CommittedCorpus(), "no-such-harness", PINNED_OPENCODE_VERSION, ADMITTED_OPENCODE_RANGE) is None
    )


def test_reference_corpus_selection_picks_the_newest_committed_corpus_at_or_below_observed(
    tmp_path: Path,
) -> None:
    for version, classification in (("1.0.0", "supported"), ("1.5.0", "degraded")):
        manifest_dir = tmp_path / "widget" / version
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "manifest.json").write_text(json.dumps({"live_evidence": {"classification": classification}}))
    admitted_range = SpecifierSet(">=1.0.0,<2.0")

    # Below both: no reference corpus qualifies.
    assert reference_corpus_version(CommittedCorpus(tmp_path), "widget", "0.9.0", admitted_range) is None
    # Between them: the older one is still the newest at or below.
    assert reference_corpus_version(CommittedCorpus(tmp_path), "widget", "1.2.0", admitted_range) == "1.0.0"
    assert (
        classify_offline(CommittedCorpus(tmp_path), "widget", "1.2.0", admitted_range)
        is CompatibilityClassification.SUPPORTED
    )
    # At or above the newer one: resolves to it.
    assert reference_corpus_version(CommittedCorpus(tmp_path), "widget", "1.9.0", admitted_range) == "1.5.0"
    assert (
        classify_offline(CommittedCorpus(tmp_path), "widget", "1.9.0", admitted_range)
        is CompatibilityClassification.DEGRADED
    )


def test_a_committed_corpus_outside_the_admitted_range_is_never_a_reference(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "widget" / "3.0.0"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps({"live_evidence": {"classification": "supported"}}))

    assert reference_corpus_version(CommittedCorpus(tmp_path), "widget", "3.0.0", SpecifierSet("<2.0")) is None
    assert classify_offline(CommittedCorpus(tmp_path), "widget", "3.0.0", SpecifierSet("<2.0")) is None


def test_the_corpus_is_injectable(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "widget" / "2.0.0"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps({"live_evidence": {"classification": "supported"}}))
    admitted_range = SpecifierSet(">=2.0.0,<3.0")

    assert (
        classify_offline(CommittedCorpus(tmp_path), "widget", "2.0.0", admitted_range)
        is CompatibilityClassification.SUPPORTED
    )
    # The real corpus is never consulted when a root override is supplied.
    assert (
        classify_offline(CommittedCorpus(tmp_path), "opencode", _AN_ADMITTED_OPENCODE_VERSION, ADMITTED_OPENCODE_RANGE)
        is None
    )


def test_a_malformed_manifest_is_unknown_rather_than_raising(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "widget" / "1.0.0"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text("not json")

    assert classify_offline(CommittedCorpus(tmp_path), "widget", "1.0.0", SpecifierSet(">=1.0.0")) is None


def test_a_manifest_with_an_unrecognized_classification_label_is_unknown(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "widget" / "1.0.0"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps({"live_evidence": {"classification": "mystifying"}}))

    assert classify_offline(CommittedCorpus(tmp_path), "widget", "1.0.0", SpecifierSet(">=1.0.0")) is None


def test_assert_admitted_range_has_corpus_passes_when_a_committed_corpus_is_inside_it() -> None:
    assert_admitted_range_has_corpus(CommittedCorpus(), "opencode", ADMITTED_OPENCODE_RANGE)


def test_a_raw_prefixed_observed_version_normalizes_before_classification() -> None:
    """A raw, prefixed ``--version`` output must route through the shared normalizer before
    the corpus lookup, the same normalized form the live probe already stores
    ."""
    raw = "opencode version 1.18.25\n"
    normalized = normalize_opencode_version(raw)
    assert normalized == PINNED_OPENCODE_VERSION
    assert (
        classify_offline(CommittedCorpus(), "opencode", normalized, ADMITTED_OPENCODE_RANGE)
        is CompatibilityClassification.DEGRADED
    )


def test_assert_admitted_range_has_corpus_raises_naming_the_range(tmp_path: Path) -> None:
    admitted_range = SpecifierSet(">=2.0.0,<3.0")

    with pytest.raises(CorpusConfigurationError, match=r">=2\.0\.0"):
        assert_admitted_range_has_corpus(CommittedCorpus(tmp_path), "widget", admitted_range)


def test_a_semver_prerelease_named_corpus_directory_is_never_admitted(tmp_path: Path) -> None:
    """A directory named ``1.19.0-1`` reads as a semver pre-release — the
    same guard :func:`~blizzard.runner.harness.admission.version_admitted`
    applies to an observed version, now shared by corpus admission too."""
    for version in ("1.19.0-1", "1.19.0"):
        manifest_dir = tmp_path / "widget" / version
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "manifest.json").write_text(json.dumps({"live_evidence": {"classification": "supported"}}))

    assert admitted_corpus_versions(CommittedCorpus(tmp_path), "widget", SpecifierSet(">=1.0.0,<2.0")) == ("1.19.0",)


class _FakeCorpus:
    """An in-memory corpus: ``manifests[(harness_id, version)]``."""

    def __init__(self, manifests: dict[tuple[str, str], dict[str, object]]) -> None:
        self._manifests = manifests

    def versions(self, harness_id: str) -> tuple[str, ...]:
        return tuple(version for harness, version in self._manifests if harness == harness_id)

    def manifest(self, harness_id: str, version: str) -> dict[str, object] | None:
        return self._manifests.get((harness_id, version))


def _fake_corpus(*versions: tuple[str, str | None]) -> ICompatibilityCorpus:
    return _FakeCorpus({("widget", v): {"live_evidence": {"classification": c}} if c else {} for v, c in versions})


def test_corpus_selection_picks_the_newest_admitted_version_at_or_below_the_observed_one_by_value() -> None:
    corpus = _fake_corpus(("1.0.0", "supported"), ("1.5.0", "degraded"), ("2.5.0", "supported"))
    admitted_range = SpecifierSet(">=1.0.0,<2.0")

    assert admitted_corpus_versions(corpus, "widget", admitted_range) == ("1.0.0", "1.5.0")
    assert reference_corpus_version(corpus, "widget", "1.9.0", admitted_range) == "1.5.0"
    assert classify_offline(corpus, "widget", "1.9.0", admitted_range) is CompatibilityClassification.DEGRADED
    assert classify_offline(corpus, "widget", "1.2.0", admitted_range) is CompatibilityClassification.SUPPORTED


def test_classification_is_none_when_the_reference_manifest_names_none_by_value() -> None:
    corpus = _fake_corpus(("1.0.0", None))

    assert classify_offline(corpus, "widget", "1.0.0", SpecifierSet(">=1.0.0")) is None


def test_an_admitted_range_with_no_fake_corpus_inside_it_raises_by_value() -> None:
    with pytest.raises(CorpusConfigurationError):
        assert_admitted_range_has_corpus(_fake_corpus(("3.0.0", "supported")), "widget", SpecifierSet("<2.0"))

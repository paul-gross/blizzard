"""``classify_offline``'s corpus lookup (blizzard#438) — reads the committed fixture corpus
rather than running a live probe. Mirrors `test_runner_harness_opencode_compatibility.py`'s
own `_PACKAGE_ROOT`/corpus-path construction.

``classify_offline`` classifies a version by resolving it to a *reference corpus* — the
newest committed corpus at or below it, inside the admitted range — never by requiring a
corpus for the exact observed version. It carries no membership concept of its own (D2): the
admitted range is used only to select the reference corpus, never to assert the observed
version is itself admitted — a caller (`capability_snapshot.py`) checks that membership
itself, before consulting this classification. The two-facts-not-one distinction that
membership check exists for is pinned at the evaluation-policy level instead
(`tests/test_runner_harness_health.py`, `tests/test_runner_harness_health_cache.py`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

from blizzard.runner.harness.compatibility import CompatibilityClassification
from blizzard.runner.harness.internal.harness_shared import normalize_opencode_version
from blizzard.runner.harness.internal.offline_compatibility import (
    DEFAULT_CORPUS_ROOT,
    CorpusConfigurationError,
    admitted_corpus_versions,
    assert_admitted_range_has_corpus,
    classify_offline,
    reference_corpus_version,
)
from blizzard.runner.harness.internal.opencode_probe import ADMITTED_OPENCODE_RANGE, PINNED_OPENCODE_VERSION

pytestmark = pytest.mark.unit

_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "blizzard" / "runner" / "harness"
# Keyed off the admitted range's own committed corpus (blizzard#438), not a hardcoded
# literal — there is exactly one committed corpus today, but this stays correct once a
# second one lands.
_AN_ADMITTED_OPENCODE_VERSION = admitted_corpus_versions("opencode", ADMITTED_OPENCODE_RANGE)[0]
_CORPUS_DIR = _PACKAGE_ROOT / "contracts" / "opencode" / _AN_ADMITTED_OPENCODE_VERSION


def test_default_corpus_root_is_the_harness_packages_own_contracts_tree() -> None:
    assert DEFAULT_CORPUS_ROOT == _PACKAGE_ROOT / "contracts"


def test_the_pinned_opencode_corpus_classifies_degraded() -> None:
    """`contracts/opencode/1.18.25/manifest.json`'s own `live_evidence.classification`
    is `"degraded"` — pinned here so a corpus edit that silently changes it is caught."""
    assert (
        classify_offline("opencode", _AN_ADMITTED_OPENCODE_VERSION, ADMITTED_OPENCODE_RANGE)
        is CompatibilityClassification.DEGRADED
    )


def test_no_observed_version_is_unknown() -> None:
    assert classify_offline("opencode", None, ADMITTED_OPENCODE_RANGE) is None


def test_a_version_above_every_committed_corpus_still_resolves_to_the_newest_one() -> None:
    """1.18.31 is inside `ADMITTED_OPENCODE_RANGE` and above the only committed corpus
    (1.18.25); it resolves to that corpus as its reference rather than reading as unknown."""
    assert reference_corpus_version("opencode", "1.18.31", ADMITTED_OPENCODE_RANGE) == _AN_ADMITTED_OPENCODE_VERSION
    assert classify_offline("opencode", "1.18.31", ADMITTED_OPENCODE_RANGE) is CompatibilityClassification.DEGRADED


def test_an_unparsable_observed_version_is_unknown_rather_than_raising() -> None:
    assert reference_corpus_version("opencode", "9.9.9-does-not-exist", ADMITTED_OPENCODE_RANGE) is None
    assert classify_offline("opencode", "9.9.9-does-not-exist", ADMITTED_OPENCODE_RANGE) is None


def test_an_unknown_harness_id_is_unknown() -> None:
    assert classify_offline("no-such-harness", PINNED_OPENCODE_VERSION, ADMITTED_OPENCODE_RANGE) is None


def test_reference_corpus_selection_picks_the_newest_committed_corpus_at_or_below_observed(
    tmp_path: Path,
) -> None:
    for version, classification in (("1.0.0", "supported"), ("1.5.0", "degraded")):
        manifest_dir = tmp_path / "widget" / version
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "manifest.json").write_text(json.dumps({"live_evidence": {"classification": classification}}))
    admitted_range = SpecifierSet(">=1.0.0,<2.0")

    # Below both: no reference corpus qualifies.
    assert reference_corpus_version("widget", "0.9.0", admitted_range, corpus_root=tmp_path) is None
    # Between them: the older one is still the newest at or below.
    assert reference_corpus_version("widget", "1.2.0", admitted_range, corpus_root=tmp_path) == "1.0.0"
    assert (
        classify_offline("widget", "1.2.0", admitted_range, corpus_root=tmp_path)
        is CompatibilityClassification.SUPPORTED
    )
    # At or above the newer one: resolves to it.
    assert reference_corpus_version("widget", "1.9.0", admitted_range, corpus_root=tmp_path) == "1.5.0"
    assert (
        classify_offline("widget", "1.9.0", admitted_range, corpus_root=tmp_path)
        is CompatibilityClassification.DEGRADED
    )


def test_a_committed_corpus_outside_the_admitted_range_is_never_a_reference(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "widget" / "3.0.0"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps({"live_evidence": {"classification": "supported"}}))

    assert reference_corpus_version("widget", "3.0.0", SpecifierSet("<2.0"), corpus_root=tmp_path) is None
    assert classify_offline("widget", "3.0.0", SpecifierSet("<2.0"), corpus_root=tmp_path) is None


def test_corpus_root_is_injectable(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "widget" / "2.0.0"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps({"live_evidence": {"classification": "supported"}}))
    admitted_range = SpecifierSet(">=2.0.0,<3.0")

    assert (
        classify_offline("widget", "2.0.0", admitted_range, corpus_root=tmp_path)
        is CompatibilityClassification.SUPPORTED
    )
    # The real corpus is never consulted when a root override is supplied.
    assert (
        classify_offline("opencode", _AN_ADMITTED_OPENCODE_VERSION, ADMITTED_OPENCODE_RANGE, corpus_root=tmp_path)
        is None
    )


def test_a_malformed_manifest_is_unknown_rather_than_raising(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "widget" / "1.0.0"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text("not json")

    assert classify_offline("widget", "1.0.0", SpecifierSet(">=1.0.0"), corpus_root=tmp_path) is None


def test_a_manifest_with_an_unrecognized_classification_label_is_unknown(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "widget" / "1.0.0"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps({"live_evidence": {"classification": "mystifying"}}))

    assert classify_offline("widget", "1.0.0", SpecifierSet(">=1.0.0"), corpus_root=tmp_path) is None


def test_assert_admitted_range_has_corpus_passes_when_a_committed_corpus_is_inside_it() -> None:
    assert_admitted_range_has_corpus("opencode", ADMITTED_OPENCODE_RANGE)


def test_a_raw_prefixed_observed_version_normalizes_before_classification() -> None:
    """A raw, prefixed ``--version`` output must route through the shared normalizer before
    the corpus lookup, the same normalized form the live probe already stores
    (blizzard#438)."""
    raw = "opencode version 1.18.25\n"
    normalized = normalize_opencode_version(raw)
    assert normalized == PINNED_OPENCODE_VERSION
    assert classify_offline("opencode", normalized, ADMITTED_OPENCODE_RANGE) is CompatibilityClassification.DEGRADED


def test_assert_admitted_range_has_corpus_raises_naming_the_range(tmp_path: Path) -> None:
    admitted_range = SpecifierSet(">=2.0.0,<3.0")

    with pytest.raises(CorpusConfigurationError, match=r">=2\.0\.0"):
        assert_admitted_range_has_corpus("widget", admitted_range, corpus_root=tmp_path)


def test_a_semver_prerelease_named_corpus_directory_is_never_admitted(tmp_path: Path) -> None:
    """A directory named ``1.19.0-1`` reads as a semver pre-release (blizzard#604) — the
    same guard :func:`~blizzard.runner.harness.internal.harness_shared.version_admitted`
    applies to an observed version, now shared by corpus admission too (D2)."""
    for version in ("1.19.0-1", "1.19.0"):
        manifest_dir = tmp_path / "widget" / version
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "manifest.json").write_text(json.dumps({"live_evidence": {"classification": "supported"}}))

    assert admitted_corpus_versions("widget", SpecifierSet(">=1.0.0,<2.0"), corpus_root=tmp_path) == ("1.19.0",)

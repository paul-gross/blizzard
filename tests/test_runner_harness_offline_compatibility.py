"""``classify_offline``'s corpus lookup (blizzard#438) — reads the committed fixture corpus
rather than running a live probe. Mirrors `test_runner_harness_opencode_compatibility.py`'s
own `_PACKAGE_ROOT`/corpus-path construction.

``classify_offline`` classifies a version already known to be admitted; it carries no
membership concept of its own (D2) — a caller (`capability_snapshot.py`) checks admission
itself, before consulting this classification. The two-facts-not-one distinction that
membership check exists for is pinned at the evaluation-policy level instead
(`tests/test_runner_harness_health.py`, `tests/test_runner_harness_health_cache.py`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.runner.harness.compatibility import CompatibilityClassification
from blizzard.runner.harness.internal.harness_shared import normalize_opencode_version
from blizzard.runner.harness.internal.offline_compatibility import (
    DEFAULT_CORPUS_ROOT,
    CorpusConfigurationError,
    assert_admitted_versions_have_corpus,
    classify_offline,
)
from blizzard.runner.harness.internal.opencode_probe import ADMITTED_OPENCODE_VERSIONS, PINNED_OPENCODE_VERSION

pytestmark = pytest.mark.unit

_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "blizzard" / "runner" / "harness"
# Keyed off the admitted set itself (blizzard#438, F19), not the legacy single-version pin —
# there is exactly one member today, but this stays correct as the set grows.
_AN_ADMITTED_OPENCODE_VERSION = sorted(ADMITTED_OPENCODE_VERSIONS)[0]
_CORPUS_DIR = _PACKAGE_ROOT / "contracts" / "opencode" / _AN_ADMITTED_OPENCODE_VERSION


def test_default_corpus_root_is_the_harness_packages_own_contracts_tree() -> None:
    assert DEFAULT_CORPUS_ROOT == _PACKAGE_ROOT / "contracts"


def test_the_pinned_opencode_corpus_classifies_degraded() -> None:
    """`contracts/opencode/1.18.25/manifest.json`'s own `live_evidence.classification`
    is `"degraded"` — pinned here so a corpus edit that silently changes it is caught."""
    assert classify_offline("opencode", PINNED_OPENCODE_VERSION) is CompatibilityClassification.DEGRADED


def test_no_observed_version_is_unknown() -> None:
    assert classify_offline("opencode", None) is None


def test_a_version_with_no_corpus_entry_is_unknown() -> None:
    assert classify_offline("opencode", "9.9.9-does-not-exist") is None


def test_an_unknown_harness_id_is_unknown() -> None:
    assert classify_offline("no-such-harness", PINNED_OPENCODE_VERSION) is None


def test_corpus_root_is_injectable(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "widget" / "2.0.0"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps({"live_evidence": {"classification": "supported"}}))

    assert classify_offline("widget", "2.0.0", corpus_root=tmp_path) is CompatibilityClassification.SUPPORTED
    # The real corpus is never consulted when a root override is supplied.
    assert classify_offline("opencode", PINNED_OPENCODE_VERSION, corpus_root=tmp_path) is None


def test_a_malformed_manifest_is_unknown_rather_than_raising(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "widget" / "1.0.0"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text("not json")

    assert classify_offline("widget", "1.0.0", corpus_root=tmp_path) is None


def test_a_manifest_with_an_unrecognized_classification_label_is_unknown(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "widget" / "1.0.0"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps({"live_evidence": {"classification": "mystifying"}}))

    assert classify_offline("widget", "1.0.0", corpus_root=tmp_path) is None


def test_assert_admitted_versions_have_corpus_passes_when_every_version_has_a_manifest() -> None:
    assert_admitted_versions_have_corpus("opencode", ADMITTED_OPENCODE_VERSIONS)


def test_a_raw_prefixed_observed_version_normalizes_before_classification() -> None:
    """A raw, prefixed ``--version`` output must route through the shared normalizer before
    the corpus lookup, the same normalized form the live probe already stores
    (blizzard#438)."""
    raw = "opencode version 1.18.25\n"
    normalized = normalize_opencode_version(raw)
    assert normalized == PINNED_OPENCODE_VERSION
    assert classify_offline("opencode", normalized) is CompatibilityClassification.DEGRADED


def test_assert_admitted_versions_have_corpus_raises_naming_the_missing_version(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "widget" / "1.0.0"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps({"live_evidence": {"classification": "supported"}}))

    with pytest.raises(CorpusConfigurationError, match=r"2\.0\.0"):
        assert_admitted_versions_have_corpus("widget", frozenset({"1.0.0", "2.0.0"}), corpus_root=tmp_path)

"""``classify_offline``'s corpus lookup (blizzard#438) — reads the committed fixture corpus
rather than running a live probe. Mirrors `test_runner_harness_opencode_compatibility.py`'s
own `_PACKAGE_ROOT`/corpus-path construction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.runner.harness.compatibility import CompatibilityClassification
from blizzard.runner.harness.internal.offline_compatibility import DEFAULT_CORPUS_ROOT, classify_offline
from blizzard.runner.harness.internal.opencode_probe import PINNED_OPENCODE_VERSION

pytestmark = pytest.mark.unit

_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "blizzard" / "runner" / "harness"
_CORPUS_DIR = _PACKAGE_ROOT / "contracts" / "opencode" / PINNED_OPENCODE_VERSION


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

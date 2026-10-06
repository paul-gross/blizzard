"""``OpenCodeHealthProbe``/``ClaudeCodeHealthProbe`` driven against the real fixture corpus
 — a domain slice wired with real internal collaborators (the committed
manifest), doubles only at the seam a live subprocess would otherwise cross: `binary_present`
and version observation are stubbed inline (the plan's own acceptance), never a fake process."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from packaging.version import Version

from blizzard.runner.harness.claude_code.health import ADMITTED_CLAUDE_CODE_RANGE, ClaudeCodeHealthProbe
from blizzard.runner.harness.compatibility import CompatibilityProbe
from blizzard.runner.harness.offline_compatibility import DEFAULT_CORPUS_ROOT, admitted_corpus_versions
from blizzard.runner.harness.opencode.compatibility.probe import ADMITTED_OPENCODE_RANGE
from blizzard.runner.harness.opencode.health import OpenCodeHealthProbe
from tests.repo_files import repo_root

pytestmark = pytest.mark.component

_PACKAGE_ROOT = repo_root() / "src" / "blizzard" / "runner" / "harness"
# Keyed off the admitted range's own committed corpus — there is exactly
# one committed corpus today, but this stays correct once a second one lands.
_AN_ADMITTED_OPENCODE_VERSION = admitted_corpus_versions("opencode", ADMITTED_OPENCODE_RANGE)[0]
_CORPUS_DIR = _PACKAGE_ROOT / "contracts" / "opencode" / _AN_ADMITTED_OPENCODE_VERSION


def _manifest() -> dict:
    return json.loads((_CORPUS_DIR / "manifest.json").read_text())


def test_opencode_health_probe_declares_the_pinned_versions_absences() -> None:
    probe = OpenCodeHealthProbe("opencode", auth_path=None)

    manifest = _manifest()
    assert probe.supported_version() == ADMITTED_OPENCODE_RANGE
    assert Version(manifest["version"]) in probe.supported_version()

    degradations = probe.declared_degradations()
    declared_probes = {degradation.probe for degradation in degradations}
    assert declared_probes == {
        CompatibilityProbe.ROOT_HOOK,
        CompatibilityProbe.USAGE_COST,
        CompatibilityProbe.CHILD_SESSIONS,
    }
    assert all(degradation.summary.strip() for degradation in degradations)

    # The manifest's diagnostic fixture names the narrower set observed absent for this one
    # run — a subset of what the binding declares degradable in general.
    diagnostic_degraded = set(manifest["live_evidence"]["fixtures"]["diagnostic"]["degraded"])
    assert diagnostic_degraded == {"root_hook", "child_sessions"}
    assert diagnostic_degraded <= {probe.value for probe in declared_probes}


def test_claude_code_health_probe_declares_an_admitted_range_and_no_degradations() -> None:
    probe = ClaudeCodeHealthProbe("claude")

    assert probe.supported_version() == ADMITTED_CLAUDE_CODE_RANGE
    assert probe.classifies_offline() is False
    assert probe.declared_degradations() == ()


def test_opencode_health_probe_construction_survives_a_missing_corpus_manifest(tmp_path: Path) -> None:
    """Construction over a ``corpus_root`` with no manifest does not raise.
    ``supported_version`` still reports the declared range; ``declared_degradations`` is
    empty since there is no manifest to read one from."""
    probe = OpenCodeHealthProbe("opencode", auth_path=None, corpus_root=tmp_path)

    assert probe.supported_version() == ADMITTED_OPENCODE_RANGE
    assert probe.declared_degradations() == ()


def test_opencode_health_probe_declared_degradations_come_from_the_corpus_manifest() -> None:
    """``declared_degradations`` equals the committed corpus manifest's entries, read from
    ``DEFAULT_CORPUS_ROOT``."""
    probe = OpenCodeHealthProbe("opencode", auth_path=None, corpus_root=DEFAULT_CORPUS_ROOT)

    manifest = _manifest()
    manifest_degradations = {(entry["probe"], entry["summary"]) for entry in manifest["declared_degradations"]}
    probe_degradations = {(d.probe.value, d.summary) for d in probe.declared_degradations()}
    assert probe_degradations == manifest_degradations

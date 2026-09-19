"""``OpenCodeHealthProbe``/``ClaudeCodeHealthProbe`` driven against the real fixture corpus
(blizzard#438) — a domain slice wired with real internal collaborators (the committed
manifest), doubles only at the seam a live subprocess would otherwise cross: `binary_present`
and version observation are stubbed inline (the plan's own acceptance), never a fake process."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.runner.harness.compatibility import CompatibilityProbe
from blizzard.runner.harness.internal.claude_code_health import ClaudeCodeHealthProbe
from blizzard.runner.harness.internal.opencode_health import OpenCodeHealthProbe
from blizzard.runner.harness.internal.opencode_probe import PINNED_OPENCODE_VERSION

pytestmark = pytest.mark.component

_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "blizzard" / "runner" / "harness"
_CORPUS_DIR = _PACKAGE_ROOT / "contracts" / "opencode" / PINNED_OPENCODE_VERSION


def _manifest() -> dict:
    return json.loads((_CORPUS_DIR / "manifest.json").read_text())


def test_opencode_health_probe_declares_the_pinned_versions_absences() -> None:
    probe = OpenCodeHealthProbe("opencode")

    manifest = _manifest()
    assert probe.supported_version() == manifest["version"] == PINNED_OPENCODE_VERSION

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


def test_claude_code_health_probe_declares_no_version_or_degradations() -> None:
    probe = ClaudeCodeHealthProbe("claude")

    assert probe.supported_version() is None
    assert probe.declared_degradations() == ()

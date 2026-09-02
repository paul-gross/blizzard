"""Composition boundary for the concrete OpenCode compatibility diagnostic."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.runner.harness.compatibility import CompatibilityDiagnostic, CompatibilityReport
from blizzard.runner.harness.internal.opencode_evidence import OpenCodeEvidence
from blizzard.runner.harness.internal.opencode_probe import OpenCodeCompatibilityProbe


def run_opencode_compatibility(
    probe: OpenCodeCompatibilityProbe,
    evidence: OpenCodeEvidence,
) -> CompatibilityReport:
    """Run the deterministic coordinator, then retain only sanitized process evidence."""

    evidence.validate()
    report = CompatibilityDiagnostic(probe).run()
    runtime: Mapping[str, object] = probe.evidence
    evidence.write(report, runtime)
    return report


__all__ = ["run_opencode_compatibility"]

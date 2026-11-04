"""Component proof for the hermetic OpenCode compatibility diagnostic. Cases that drive the
fake OpenCode-shaped CLI as an out-of-process subprocess live in
``tests/service/harnesses/test_opencode_compatibility_service.py`` (``bzh:external-cli-fake-is-service-tier``).

This module holds:

- Pure domain logic with no subprocess at all (shape classification, evidence-path validation,
  provider-refusal parsing, isolation-root construction).
- A case that resolves an executable path only to prove a pre-flight rejection happens before the
  process would ever be spawned (a missing compactor, a malformed model, an unusable evidence
  destination, an interrupted/failing injected process fake) — "binary-free": a two-line inline
  stub script stands in, since nothing here ever runs it as an OpenCode-shaped CLI.
- A generic subprocess/pty/Landlock-sandbox boundary exercised directly (``sys.executable``, a
  disposable-auth shell wrapper), with no external-tool-shaped CLI in the loop at all
  (`blizzard-context:verification/blizzard/tier-rules.md`'s "third bucket").
"""

from __future__ import annotations

import pytest

from blizzard.runner.harness.compatibility import (
    PROBE_ROSTER,
    CompatibilityDiagnostic,
    CompatibilityProbe,
    IncompleteProbeReportError,
    ProbeObservation,
)
from blizzard.runner.harness.opencode.compatibility.probe import (
    ADMITTED_OPENCODE_RANGE_DISPLAY,
    PINNED_OPENCODE_VERSION,
)


class _ContractProbe:
    observed_version = PINNED_OPENCODE_VERSION
    admitted_range = ADMITTED_OPENCODE_RANGE_DISPLAY
    version_admitted = True

    def __init__(self, observations: list[ProbeObservation]) -> None:
        self.observations = observations

    def run(self) -> list[ProbeObservation]:
        return self.observations


def _observations() -> list[ProbeObservation]:
    return [ProbeObservation.observed(probe, f"{probe.value} observed") for probe in PROBE_ROSTER]


def test_diagnostic_rejects_missing_results_before_evidence_can_be_published() -> None:
    observations = _observations()[:-1]

    with pytest.raises(IncompleteProbeReportError, match="missing probes"):
        CompatibilityDiagnostic(_ContractProbe(observations)).run()


def test_diagnostic_keeps_an_ambiguous_result_blocking() -> None:
    observations = _observations()
    observations[0] = ProbeObservation.ambiguous(CompatibilityProbe.FRESH_TURN, "two possible outcomes")

    report = CompatibilityDiagnostic(_ContractProbe(observations)).run()

    assert report.complete is True
    assert report.classification.value == "blocking"
    assert report.results[0].classification.value == "blocking"
    assert report.admissible is False

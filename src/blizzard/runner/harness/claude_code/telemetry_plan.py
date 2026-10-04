"""What the runner knows of Claude Code's own telemetry: the instrumentation scopes it exports under, the
``service.name`` its telemetry leaves the runner with, and the per-signal plan for pointing a worker's exporters
at the runner.

The plan type and the names live in ``harness/harness_telemetry_plan.py``. The plan is derived once, at the
composition root; Claude Code's name rules live in ``claude_code/telemetry.py``."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from blizzard.foundation.harness_telemetry_outcome import HarnessTelemetryOutcome
from blizzard.foundation.platform_tracing.signals import TelemetrySignal, signal_exportable
from blizzard.runner.harness.bundle import BundleSnapshot
from blizzard.runner.harness.claude_code.bundle import ClaudeCodeBundleDelivery
from blizzard.runner.harness.claude_code.section import ClaudeCodeSection
from blizzard.runner.harness.claude_code.telemetry import RUNNER_OWNED_NAMES, operator_configured
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryPlan
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.wiring import section_of

if TYPE_CHECKING:
    from blizzard.runner.config import RunnerConfig

__all__ = ["plan_harness_telemetry"]


def plan_harness_telemetry(
    config: RunnerConfig,
    *,
    bundle: BundleSnapshot | None,
    runner_environ: Mapping[str, str],
    enabled: bool,
) -> HarnessTelemetryPlan:
    """The plan for a Claude Code worker spawned under ``config``. ``enabled`` is ``[tracing] harness_telemetry``
    together with platform tracing. The operator's destination is read from the worker's allowlisted env and
    from the ``env`` of the settings document the adapter passes. Every signal is off where the runner has no
    enabled Claude Code binding."""
    if not enabled or not section_of(config.harness_sections, CLAUDE_CODE_HARNESS_ID).enabled:
        return HarnessTelemetryPlan()
    spawn_env = {k: v for k, v in config.worker_env.variables.items() if k not in RUNNER_OWNED_NAMES}
    visible = {**spawn_env, **_settings_env(_effective_settings_path(config, bundle))}

    def outcome(signal: TelemetrySignal) -> HarnessTelemetryOutcome:
        if operator_configured(signal, visible):
            return HarnessTelemetryOutcome.OPERATOR_CONFIGURED
        if not signal_exportable(signal, runner_environ):
            return HarnessTelemetryOutcome.NO_RUNNER_DESTINATION
        return HarnessTelemetryOutcome.CAPTURED

    return HarnessTelemetryPlan(
        traces=outcome(TelemetrySignal.TRACES),
        metrics=outcome(TelemetrySignal.METRICS),
        logs=outcome(TelemetrySignal.LOGS),
    )


def _effective_settings_path(config: RunnerConfig, bundle: BundleSnapshot | None) -> str | None:
    """The document the adapter passes as ``--settings``: the published bundle's composed one, else the runner's own."""
    delivery = ClaudeCodeBundleDelivery.of(bundle)
    if delivery is not None:
        return str(delivery.settings)
    section = section_of(config.harness_sections, CLAUDE_CODE_HARNESS_ID)
    assert isinstance(section, ClaudeCodeSection)
    return section.worker_settings_path


def _settings_env(path: str | None) -> dict[str, str]:
    """The string entries of the settings document's ``env``; empty when there is no readable document."""
    if not path:
        return {}
    try:
        document = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    env = document.get("env") if isinstance(document, dict) else None
    if not isinstance(env, dict):
        return {}
    return {str(k): v for k, v in env.items() if isinstance(v, str)}

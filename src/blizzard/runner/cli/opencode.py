"""Runner-owned OpenCode compatibility diagnostics."""

from __future__ import annotations

from pathlib import Path

import click

from blizzard.runner.harness.compatibility import CompatibilityContractError
from blizzard.runner.harness.internal.opencode_attach import LoopbackAttachProxyFactory
from blizzard.runner.harness.internal.opencode_compaction import SubprocessOpenCodeCompactor
from blizzard.runner.harness.internal.opencode_diagnostic import run_opencode_compatibility
from blizzard.runner.harness.internal.opencode_evidence import OpenCodeEvidence, OpenCodeEvidenceError
from blizzard.runner.harness.internal.opencode_loopback import UrllibLoopbackTransport
from blizzard.runner.harness.internal.opencode_probe import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    OpenCodeCompatibilityProbe,
    OpenCodeProbeError,
)
from blizzard.runner.harness.internal.opencode_process import SubprocessOpenCodeProcess, stop_started_process
from blizzard.runner.harness.internal.opencode_scratch_git import SubprocessOpenCodeScratchGit


@click.group("opencode")
def opencode_group() -> None:
    """Read-only diagnostics for the pinned OpenCode compatibility contract."""


@opencode_group.command("compatibility")
@click.option(
    "--binary",
    "binary",
    required=True,
    type=click.Path(exists=True, dir_okay=False, resolve_path=True, path_type=Path),
    help="Explicit OpenCode executable to probe.",
)
@click.option("--model", "model", required=True, help="Explicit provider/model reference.")
@click.option("--variant", "variant", required=True, help="Explicit OpenCode model variant.")
@click.option(
    "--evidence-dir",
    "evidence_directory",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory for sanitized proof evidence.",
)
@click.option(
    "--live-provider",
    "--allow-live-provider",
    "live_provider",
    is_flag=True,
    required=True,
    help="Required explicit opt-in to provider-reaching probes.",
)
def opencode_compatibility(
    binary: Path,
    model: str,
    variant: str,
    evidence_directory: Path,
    live_provider: bool,
) -> None:
    """Prove OpenCode 1.18.25 in a disposable git repository and retain sanitized evidence."""
    try:
        process = SubprocessOpenCodeProcess()
        transport = UrllibLoopbackTransport()
        probe = OpenCodeCompatibilityProbe(
            binary=str(binary),
            model=model,
            variant=variant,
            scratch_git=SubprocessOpenCodeScratchGit(),
            process=process,
            compactor=SubprocessOpenCodeCompactor(
                process,
                stop_started_process,
                transport,
                timeout_seconds=DEFAULT_COMMAND_TIMEOUT_SECONDS,
            ),
            transport=transport,
            attach_proxy_factory=LoopbackAttachProxyFactory(transport),
            allow_live_provider=live_provider,
        )
        report = run_opencode_compatibility(
            probe,
            OpenCodeEvidence(evidence_directory, secrets=probe.secret_values),
        )
    except (CompatibilityContractError, OpenCodeEvidenceError, OpenCodeProbeError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"OpenCode version: {report.observed_version}")
    for result in report.results:
        click.echo(f"{result.probe.value}: {result.classification.value} ({result.state.value}) — {result.summary}")
    click.echo(f"compatibility: {report.classification.value}")
    if not report.admissible:
        raise click.exceptions.Exit(1)


__all__ = ["opencode_compatibility", "opencode_group"]

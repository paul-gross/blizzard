from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import click

from blizzard.cli.operator_trace import ITraceHeaders, OperatorTrace
from blizzard.foundation.store.migrations import RevisionMismatchError, StoreBelowFloorError
from blizzard.runner.cli.daemon import daemon_holding, runner_option
from blizzard.runner.cli.directory import load_runner
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.cli.runtime import configured_ids, named_runners
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_layout import HostHeld, HostLayout, HostLock
from blizzard.runner.lifecycle.identity_check import foreign_store_reason
from blizzard.runner.loop_wiring import LoopWiring
from blizzard.runner.runtime import ensure_current_revision, reset_command
from blizzard.runner.transcripts.backfill import TranscriptReshipError


@click.group("transcript")
def transcript_group() -> None:
    """Operator: maintenance over this runner's own transcript lane."""


@contextmanager
def _transcript_config(
    directory: str, *, runner: str | None, verb: str, trace: ITraceHeaders
) -> Iterator[RunnerConfig]:
    """Every store-writing transcript verb's guards, and the host directory held while the verb runs — shared so a
    second verb cannot ship with fewer of them than the first."""
    config = load_runner(directory, runner=runner)
    if config.runner_id is None:
        raise click.ClickException(
            f"runner {config.name} has not joined its hub and has no id, so it has no transcript lane to touch; "
            "run `blizzard runner init` first"
        )
    if not config.transcripts_ship:
        raise click.ClickException(f"[transcripts] ship is false — enable the lane before {verb} into it")
    layout = HostLayout(config.root)
    try:
        lock = HostLock.acquire(layout)
    except HostHeld as exc:
        raise click.ClickException(f"{exc} — stop it first: this verb writes the store, which is single-owner") from exc
    with lock:
        holding = daemon_holding(layout.legacy_socket_path, trace)
        if holding is not None:
            raise click.ClickException(f"{holding} — stop it first: this verb writes the store, which is single-owner")
        try:
            ensure_current_revision(config)
        except (RevisionMismatchError, StoreBelowFloorError) as exc:
            raise click.ClickException(str(exc)) from exc
        if (
            reason := foreign_store_reason(
                named_runners(config), config.runner_id, reset_command(config.root), configured=configured_ids(config)
            )
        ) is not None:
            raise click.ClickException(f"runner {config.name} ({config.runner_id}): {reason}")
        yield config


@transcript_group.command("reship")
@click.argument("segment_id")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@runner_option
@click.pass_context
def transcript_reship(ctx: click.Context, segment_id: str, directory: str, runner: str | None) -> None:
    """Re-read SEGMENT_ID's session and ship it again under a new segment id.

    For a segment the hub holds in a form this runner has outgrown — most often one an older,
    smaller per-record cap shrank. The hub never overwrites an accepted record, so superseding
    one means a second segment: BOTH then show on the board. Same operating rules as `backfill`."""
    with _transcript_config(directory, runner=runner, verb="reshipping", trace=OperatorTrace.source(ctx)) as config:
        try:
            report = LoopWiring.of(config).reship_transcript(segment_id)
        except TranscriptReshipError as exc:
            raise click.ClickException(str(exc)) from exc
    click.echo(
        f"reshipped {report.source_segment_id} -> {report.segment_id} "
        f"({report.turns} turns, {report.shipped_bytes} bytes, session {report.session_id})"
    )
    if report.shipping_stopped_reason:
        # Reads as a clean run otherwise: a stopped segment counts as caught up, so `complete`
        # is true and the counts are zero because nothing shipped, not because nothing existed.
        click.echo(
            f"warning: shipping is stopped for this chunk ({report.shipping_stopped_reason}) — "
            "the re-ship sent NOTHING; a re-ship spends the per-chunk budget a second time",
            err=True,
        )
    if not report.complete:
        click.echo(
            "warning: the source was not read to its end — the new segment stays open; "
            "rerun this verb to resume that same segment",
            err=True,
        )
    if report.truncated_reason:
        click.echo(
            f"warning: the new segment is itself marked truncated ({report.truncated_reason}) — "
            "see the event log for what that reason means",
            err=True,
        )


@transcript_group.command("backfill")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option("--dry-run", is_flag=True, help="Report what would import; open, drain and ship nothing.")
@click.option("--limit", type=int, default=None, help="Import at most this many sessions; defer the rest.")
@runner_option
@click.pass_context
def transcript_backfill(
    ctx: click.Context, directory: str, dry_run: bool, limit: int | None, runner: str | None
) -> None:
    """Import worker transcripts predating the outbound lane, best-effort.

    Driven from this runner's own lease records, never a sweep of the harness directory. Rerunnable:
    an already-imported session is skipped, and one left unfinished resumes. Writes the store
    directly, so run it as the runner's own user with its environment, and with the daemon stopped."""
    if limit is not None and limit < 1:
        raise click.UsageError("--limit must be at least 1")
    with _transcript_config(directory, runner=runner, verb="backfilling", trace=OperatorTrace.source(ctx)) as config:
        report = LoopWiring.of(config).backfill_transcripts(dry_run=dry_run, limit=limit)
    prefix = "would import" if dry_run else "imported"
    click.echo(f"{prefix} {report.imported}, already present {report.already_present}, gone {report.gone}")
    if report.deferred:
        click.echo(f"note: {report.deferred} session(s) deferred — rerun to pick them up")
    if report.capped:
        click.echo(
            f"warning: {report.capped} imported session(s) lost content to a cap — "
            "the hub refused it or the chunk budget is spent; see the event log",
            err=True,
        )

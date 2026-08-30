from __future__ import annotations

from pathlib import Path

import click
import httpx

from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.runner.cli.daemon import LOCAL_CLIENT_TIMEOUT
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.config import ConfigError, RunnerConfig
from blizzard.runner.loop.build import LoopWiring
from blizzard.runner.loop.transcript_backfill import TranscriptReshipError
from blizzard.runner.runtime import ensure_current_revision


@click.group("transcript")
def transcript_group() -> None:
    """Operator: maintenance over this runner's own transcript lane (blizzard#250)."""


def _daemon_holding(config: RunnerConfig) -> str | None:
    """What is holding this runtime's socket, or ``None`` when nothing is — the single-writer
    guard's probe. **Fail-closed**: only an absent socket or a refused connection is nothing
    there; a timeout or an error answer means something is on the far end, and a guard whose
    ambiguous case resolves toward "safe to write" inverts the property it protects."""
    sock = RunnerConfig.socket_path_for(config.root)
    if not sock.exists():
        return None
    transport = httpx.HTTPTransport(uds=str(sock))
    try:
        with httpx.Client(transport=transport, base_url="http://runner", timeout=LOCAL_CLIENT_TIMEOUT) as client:
            response = client.get("/api/health")
    except httpx.ConnectError:
        return None  # a socket file an ungraceful exit left behind — nothing is listening on the corpse
    except httpx.HTTPError as exc:
        return f"something holds {sock} but did not answer its health probe ({exc})"
    if response.status_code != 200:
        return f"something holds {sock} and answered its health probe {response.status_code}"
    return f"a runner daemon is serving at {sock}"


def _transcript_config(directory: str, *, verb: str) -> RunnerConfig:
    """Every store-writing transcript verb's guards — shared so a second verb cannot ship
    with fewer of them than the first."""
    try:
        config = RunnerConfig.load(Path(directory))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    if not config.transcripts_ship:
        raise click.ClickException(f"[transcripts] ship is false — enable the lane before {verb} into it")
    holding = _daemon_holding(config)
    if holding is not None:
        raise click.ClickException(f"{holding} — stop it first: this verb writes the store, which is single-writer")
    try:
        ensure_current_revision(config)
    except RevisionMismatchError as exc:
        raise click.ClickException(str(exc)) from exc
    return config


@transcript_group.command("reship")
@click.argument("segment_id")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
def transcript_reship(segment_id: str, directory: str) -> None:
    """Re-read SEGMENT_ID's session and ship it again under a new segment id.

    For a segment the hub holds in a form this runner has outgrown — most often one an older,
    smaller per-record cap shrank. The hub never overwrites an accepted record, so superseding
    one means a second segment: BOTH then show on the board. Same operating rules as `backfill`."""
    config = _transcript_config(directory, verb="reshipping")
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
            "the re-ship sent NOTHING; a re-ship spends the 64 MB per-chunk budget a second time",
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
def transcript_backfill(directory: str, dry_run: bool, limit: int | None) -> None:
    """Import worker transcripts predating the outbound lane, best-effort.

    Driven from this runner's own lease records, never a sweep of the harness directory. Rerunnable:
    an already-imported session is skipped, and one left unfinished resumes. Writes the store
    directly, so run it as the runner's own user with its environment, and with the daemon stopped."""
    if limit is not None and limit < 1:
        raise click.UsageError("--limit must be at least 1")
    config = _transcript_config(directory, verb="backfilling")
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

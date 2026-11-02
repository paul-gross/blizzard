from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from http import HTTPStatus
from typing import Any

import click

from blizzard.cli.collaborators import CliCollaborators
from blizzard.cli.get_group import GetGroup
from blizzard.cli.transcript_render import SegmentContentLines, SegmentIndexLines
from blizzard.foundation.clock import IClock
from blizzard.runner.cli.daemon import Door, RunnerDaemon, runner_option
from blizzard.runner.cli.directory import load_runner
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.cli.store_guard import configured_ids, store_write_guard
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop_wiring import LoopWiring
from blizzard.runner.transcripts.backfill import TranscriptReshipError

# The local API's own door override; `traces` and `control` declare the same variable.
_ENV_LOCAL_API_URL = "BZ_RUNNER_URL"


@click.group("transcript", cls=GetGroup)
def transcript_group() -> None:
    """Operator: read this runner's recorded transcripts, and maintain its own transcript lane."""


def _daemon_options(command: Any) -> Any:
    command = runner_option(command)
    command = click.option(
        "--runner-url",
        "runner_url",
        default=None,
        envvar=_ENV_LOCAL_API_URL,
        help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
    )(command)
    return click.option(
        "--dir",
        "directory",
        default=DEFAULT_DIR,
        envvar=ENV_RUNNER_DIR,
        help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
    )(command)


def _show(body: dict[str, Any], as_json: bool, view: SegmentIndexLines | SegmentContentLines) -> None:
    if as_json:
        click.echo(json.dumps(body, indent=2))
        return
    for line in view.lines():
        click.echo(line)


@transcript_group.command("list")
@click.argument("chunk_id")
@click.option("--json", "as_json", is_flag=True, default=False, help="Print the response body as JSON.")
@_daemon_options
@click.pass_context
def transcript_list(
    ctx: click.Context, chunk_id: str, as_json: bool, directory: str, runner_url: str | None, runner: str | None
) -> None:
    """List the transcript segments this runner holds for CHUNK_ID — id, node, epoch, turn range, whether it is
    closed or cut short, and size.

    Reads the running runner, so the host must be up. A chunk this runner never ran lists none. Pass a segment id to
    `transcript get` to read it."""
    with RunnerDaemon.reach("transcript list", directory, runner_url, Door.of(ctx), runner) as daemon:
        body = daemon.get(f"/api/chunks/{chunk_id}/transcripts").json()
    _show(body, as_json, SegmentIndexLines(body))


@transcript_group.command("get")
@click.argument("chunk_id")
@click.argument("segment_id")
@click.option("--json", "as_json", is_flag=True, default=False, help="Print the response body as JSON.")
@_daemon_options
@click.pass_context
def transcript_get(
    ctx: click.Context,
    chunk_id: str,
    segment_id: str,
    as_json: bool,
    directory: str,
    runner_url: str | None,
    runner: str | None,
) -> None:
    """Show SEGMENT_ID of CHUNK_ID as this runner holds it, turn by turn: each turn's time and kind, a tool turn's
    name, input and output, a subagent's turns indented under the call that spawned it.

    Reads the running runner, so the host must be up. Refused when the harness that recorded the segment is not
    installed here."""
    with RunnerDaemon.reach("transcript get", directory, runner_url, Door.of(ctx), runner) as daemon:
        resp = daemon.send("get", f"/api/chunks/{chunk_id}/transcripts/{segment_id}")
    if resp.status_code == HTTPStatus.NOT_FOUND:
        raise click.ClickException(f"this runner holds no segment {segment_id} for chunk {chunk_id}")
    if resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE:
        raise click.ClickException(
            f"segment {segment_id} cannot be read here: {resp.json().get('detail', 'unavailable')}"
        )
    resp.raise_for_status()
    body = resp.json()
    _show(body, as_json, SegmentContentLines(body))


@contextmanager
def _transcript_config(
    directory: str, *, runner: str | None, verb: str, action: str, door: Door, clock: IClock
) -> Generator[RunnerConfig, None, None]:
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
    with store_write_guard(
        config,
        action=action,
        door=door,
        clock=clock,
        runners={config.runner_id: config.name},
        configured=configured_ids(config),
        refuse_foreign=True,
    ):
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
    with _transcript_config(
        directory,
        runner=runner,
        verb="reshipping",
        action="reship a transcript",
        door=Door.of(ctx),
        clock=CliCollaborators.of(ctx).wall_clock,
    ) as config:
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
    """Import the transcripts of sessions this runner ran before it shipped transcripts, best-effort.

    Rerunnable: an already-imported session is skipped, and one left unfinished resumes. Run it as the
    runner's own user with its environment; refused while the host is running."""
    if limit is not None and limit < 1:
        raise click.UsageError("--limit must be at least 1")
    with _transcript_config(
        directory,
        runner=runner,
        verb="backfilling",
        action="backfill transcripts",
        door=Door.of(ctx),
        clock=CliCollaborators.of(ctx).wall_clock,
    ) as config:
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

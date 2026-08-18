"""``blizzard runner <cmd>`` — the machine-local surface.

Client verbs are pure clients of the runner's local API; ``host`` *becomes* the runner daemon.
Worker-hook verbs take their identity from the spawn-injected environment and pass no identity
arguments."""

from __future__ import annotations

import difflib
import json
import os
import signal
import subprocess
import time
import types
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import click
import httpx
import uvicorn
from sqlalchemy.exc import SQLAlchemyError

from blizzard.cli.host_directory import HostDirectory
from blizzard.foundation.events.server import EarlyShutdownServer
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactScope
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli_daemon import LOCAL_CLIENT_TIMEOUT, RunnerDaemon
from blizzard.runner.cli_worker import ENV_ELICITATION, WorkerCall
from blizzard.runner.config import CONFIG_FILENAME, ConfigError, RunnerConfig
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.harness.workspace_prompts import (
    PACKAGED,
    WORKSPACE_PROMPT_FILENAME,
    UnknownWorkspacePromptSample,
)
from blizzard.runner.listeners import ListenerError, Listeners, Uds
from blizzard.runner.loop.build import (
    LoopWiring,
    PeriodicDriver,
    ResumeMarking,
)
from blizzard.runner.loop.transcript_backfill import TranscriptReshipError
from blizzard.runner.runtime import ensure_current_revision, init_environment, migrate, migration_runner
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore

ENV_TICK_SECONDS = "BZ_RUNNER_TICK_SECONDS"
DEFAULT_TICK_SECONDS = 30.0

# The runtime root the dir-taking verbs resolve, highest to lowest: an explicit `--dir`, then
# `BZ_RUNNER_DIR`, then the cwd (issue #39). Selectable, not shareable — the store is single-writer.
ENV_RUNNER_DIR = "BZ_RUNNER_DIR"
DEFAULT_DIR = "."

# The operator's TCP door onto the local API (issue #43) — the override for when the socket is not
# the right address. `BZ_*` is the operator's config namespace, distinct from the worker's
# spawn-injected `BLIZZARD_*` one, which `cli_worker` owns.
ENV_LOCAL_API_URL = "BZ_RUNNER_URL"
# Each `selftest` poll is a machine-local read of already-computed state, so a short interval is free.
_SELFTEST_POLL_INTERVAL = 0.2
# A CLI-side backstop above the server's own authoritative run budget, so the CLI never spins forever
# against a runner that cannot reach that code.
_SELFTEST_POLL_TIMEOUT = 600.0

# Bounds uvicorn's own connection-drain wait — defense-in-depth, not the actual fix
# for an SSE response held open (`EarlyShutdownServer` above, D1/D3).
_GRACEFUL_SHUTDOWN_SECONDS = 5


def _set_local_paused(*, paused: bool, by: str, directory: str, runner_url: str | None) -> None:
    """PATCH the runner singleton's own pause brake — the declarative pattern applied locally."""
    with RunnerDaemon.reach("pause" if paused else "start", directory, runner_url) as daemon:
        view = daemon.patch("/api/runner", json_body={"paused": paused, "by": by}).json()
    if paused:
        click.echo(f"runner {view['runner_id']} is now locally paused — it starts no new workers")
        if view.get("hub_paused"):
            click.echo(
                f"note: it is also paused at the hub — `blizzard hub runner resume {view['runner_id']}` clears that one"
            )
        return
    click.echo(f"runner {view['runner_id']} is no longer locally paused")
    if view.get("hub_paused"):
        click.echo(
            f"note: it stays paused at the hub — clear that with `blizzard hub runner resume {view['runner_id']}`"
        )


@click.group(invoke_without_command=True)
@click.pass_context
def runner(ctx: click.Context) -> None:
    """Talk to — or become — the blizzard runner."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(host)


@runner.command()
@click.argument("directory", default=DEFAULT_DIR, envvar=ENV_RUNNER_DIR)
def init(directory: str) -> None:
    """Scaffold config + data dir + a migrated store under DIRECTORY. Idempotent.

    DIRECTORY defaults to $BZ_RUNNER_DIR, then the cwd."""
    try:
        config = init_environment(Path(directory))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    revision = migration_runner(config).current_revision()
    click.echo(f"runner runtime ready at {config.root} (store revision {revision})")


@runner.command("migrate")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option("--down", default=None, help="Reverse migrations down to this revision (e.g. base).")
def migrate_cmd(directory: str, down: str | None) -> None:
    """Apply pending store migrations, or reverse with --down <rev>."""
    try:
        migrate(Path(directory), down=down)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("migrated" if down is None else f"reversed to {down}")


@runner.command()
@click.argument("directory", required=False, default=None)
@click.option(
    "--dir",
    "dir_option",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option("--host", "host_", default=None, help="Bind host (overrides config).")
@click.option("--port", type=int, default=None, help="Bind port (overrides config).")
def host(directory: str | None, dir_option: str, host_: str | None, port: int | None) -> None:
    """Become the blizzard-runner daemon: the reconciliation loop + the local API.

    DIRECTORY (positional) and --dir are equivalent — pass one; giving both requires
    they agree. Defaults to $BZ_RUNNER_DIR, then the cwd."""
    directory = HostDirectory(directory, dir_option).path
    try:
        config = RunnerConfig.load(Path(directory), host=host_, port=port)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        ensure_current_revision(config)
    except RevisionMismatchError as exc:
        raise click.ClickException(str(exc)) from exc
    # One broker for the process (D2): `host` is the one composer building both the
    # served app and the ticked loop, so every writer and the stream route share it.
    broker = EventBroker()
    app = build_hosted_app(config, events=broker)
    interval = float(os.environ.get(ENV_TICK_SECONDS, DEFAULT_TICK_SECONDS))
    # `PeriodicDriver` resolves its prompt files on this thread, not in the loop thread: a
    # configured-but-missing prompt raises here, before any socket binds.
    try:
        driver = PeriodicDriver(config, interval_seconds=interval, broker=broker)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    # Two doors onto the one app (issue #43), bound up front so a clash fails startup loudly and
    # served by the single `Server` below, which keeps the shutdown path on one frame.
    try:
        sockets = Listeners.of(config).bound()
    except ListenerError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"serving blizzard-runner on {config.host}:{config.port} and {config.socket_path} (loop tick {interval}s)"
    )

    # The shared early-shutdown wrapper (D1/D3): sets `app.state.shutdown` ahead of
    # uvicorn's own drain, so `server.run()` returns and the `finally` below still runs.
    server = EarlyShutdownServer(
        uvicorn.Config(app, host=config.host, port=config.port, timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_SECONDS),
        shutdown_signal=app.state.shutdown,
    )

    # Installed before `server.run()`'s own `capture_signals()` window opens, so a signal in
    # that gap still primes shutdown (D3) rather than being discarded; re-invoking it later is idempotent.
    def _handle_signal(signum: int, frame: types.FrameType | None) -> None:
        server.handle_exit(signum, frame)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Ungraceful-restart recovery (#13): a `kill -9` never ran the graceful shutdown marker below, so
    # sessions killed mid-work are marked here for the same startup RESUME the first tick runs.
    resumable = ResumeMarking(config).on_startup()
    if resumable:
        click.echo(f"marked {resumable} crash-interrupted lease(s) for restart-resume")

    driver.start()  # startup recovery is REAP running first inside the tick
    try:
        server.run(sockets=sockets)
    finally:
        # Stop the loop first so no in-flight tick races the marking: `stop()` blocks on the tick
        # thread, so the loop is quiescent before every in-flight lease is marked.
        driver.stop()
        marked = ResumeMarking(config).on_shutdown()
        if marked:
            click.echo(f"marked {marked} in-flight lease(s) for restart-resume")
        # uvicorn closes a pre-bound socket but does not unlink its file; leaving it would
        # make the next start take the stale-corpse path in `Uds.bound` for nothing.
        Uds(config.socket_path).unlink()


@runner.command("tick")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
def tick_cmd(directory: str) -> None:
    """Run ONE synchronous reconciliation tick (REAP → PULL → FILL → ADVANCE).

    The steppable-loop driver for tests and the e2e (``bzh:steppable-loop``): a single pass against the
    live hub and workspace, then exit. Refuses on a store revision mismatch, like ``host``."""
    try:
        config = RunnerConfig.load(Path(directory))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        ensure_current_revision(config)
    except RevisionMismatchError as exc:
        raise click.ClickException(str(exc)) from exc
    LoopWiring.of(config).tick_once()
    click.echo("tick complete")


@runner.group("external-usage")
def external_usage_group() -> None:
    """Diagnostics for the runner's own external-subscription usage sampling (issue #218)."""


@external_usage_group.command("probe")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
def external_usage_probe(directory: str) -> None:
    """Sample the harness's own subscription rate-limit usage and print it. Read-only.

    Builds the same adapter the reconciliation loop uses and samples through it directly — a diagnostic
    seam-check (issue #218): no store write, no tick, nothing enqueued or delivered."""
    try:
        config = RunnerConfig.load(Path(directory))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    harness = ClaudeCodeAdapter(
        binary=config.harness_binary,
        settings_path=config.worker_settings_path,
        permission_mode=config.harness_permission_mode,
        model_aliases=config.model_aliases,
        effort_aliases=config.effort_aliases,
        credentials_path=config.external_usage_credentials_path,
    )
    snapshot = harness.sample_external_subscription_usage()
    if snapshot is None:
        click.echo("no sample: the harness reported nothing (see the warning log for why)")
        return
    click.echo(f"sampled at {iso_utc(snapshot.sampled_at)}")
    if not snapshot.windows:
        click.echo("  (no windows reported)")
    for window in snapshot.windows:
        click.echo(
            f"  {window.window}: {window.utilization_pct:.1f}% used, "
            f"resets at {iso_utc(window.resets_at)} (window {window.window_seconds}s)"
        )


@runner.command()
def heartbeat() -> None:
    """Worker hook: record a lease heartbeat (identity from the environment).

    Fails **soft**: a hook must never break the worker's tool call, so a missing identity or an
    unreachable runner is reported to stderr and this still exits 0."""
    worker = WorkerCall.hook("heartbeat")
    if worker is not None:
        worker.soft_post(
            "/api/heartbeat", failure="could not reach the runner", json_body={"lease_id": worker.lease_id}
        )


@runner.command("session-end")
def session_end() -> None:
    """Worker hook: record the session's exit (identity from the environment) — the worker's
    "declared done" signal.

    Fails **soft**, like the heartbeat: a hook must never break the worker's exit, so a failure
    is reported to stderr and this still exits 0."""
    if os.environ.get(ENV_ELICITATION):
        return
    worker = WorkerCall.hook("session-end")
    if worker is not None:
        worker.soft_post(worker.leased("session-end"), failure="could not reach the runner")


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


@runner.group("prompt")
def prompt_group() -> None:
    """Operator: the packaged workspace-prompt samples, and which one this runtime uses."""


_PROMPT_DIR_OPTION = click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)


_OVERRIDE_SOURCE = "store override (PUT /api/workspace-prompt)"


def _packaged_sample(name: str) -> str:
    """The named sample's prose, as a CLI error naming the corpus when there is no such sample."""
    try:
        return PACKAGED.text(name)
    except UnknownWorkspacePromptSample as exc:
        packaged = ", ".join(PACKAGED.names) or "none"
        raise click.ClickException(f"no packaged sample named {name} (packaged: {packaged})") from exc


def _prompt_config(directory: str) -> RunnerConfig:
    try:
        return RunnerConfig.load(Path(directory))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


@prompt_group.command("list")
def prompt_list() -> None:
    """List the workspace-prompt samples shipped in this wheel."""
    names = PACKAGED.names
    if not names:
        click.echo("no packaged samples")
        return
    for name in names:
        click.echo(f"{name}\t{len(PACKAGED.text(name))} characters")


@prompt_group.command("show")
@click.argument("name")
def prompt_show(name: str) -> None:
    """Print packaged sample NAME's prose to stdout."""
    click.echo(_packaged_sample(name))


@prompt_group.command("install")
@click.argument("name")
@_PROMPT_DIR_OPTION
@click.option("--force", is_flag=True, help="Overwrite an existing workspace-prompt.md in the runtime root.")
def prompt_install(name: str, directory: str, force: bool) -> None:
    """Copy packaged sample NAME into the runtime root and point the config at the copy.

    The forked shape: the config carries `workspace_prompt_file`, never the package knob, so
    `prompt diff` has a local file to compare. Takes effect on the next runner restart."""
    text = _packaged_sample(name)
    config = _prompt_config(directory)
    destination = config.root / WORKSPACE_PROMPT_FILENAME
    if destination.exists() and not force:
        raise click.ClickException(f"{destination} already exists — pass --force to overwrite it")
    destination.write_text(text)
    _repoint_config(config.root, file_path=str(destination))
    click.echo(f"installed sample {name} to {destination} and set workspace_prompt_file")
    if _stored_override(config) is not None:
        click.echo("a store override stands and wins over this file — clear it first: DELETE /api/workspace-prompt")
        return
    click.echo("restart the runner to apply it — the prompt file is read once at `host` startup")


@prompt_group.command("diff")
@click.argument("name")
@_PROMPT_DIR_OPTION
def prompt_diff(name: str, directory: str) -> None:
    """Diff this runtime's effective workspace prompt against packaged sample NAME.

    Effective means the lane a spawn reads: the store override when one stands, else the
    configured knob. Exits 1 when they differ, so a deploy can check a forked copy for drift."""
    sample = _packaged_sample(name)
    config = _prompt_config(directory)
    local, source = _effective_prompt(config)
    lines = list(
        difflib.unified_diff(
            sample.splitlines(keepends=True),
            local.splitlines(keepends=True),
            fromfile=f"packaged:{name}",
            tofile=source,
        )
    )
    if not lines:
        click.echo(f"no drift from packaged sample {name}")
        return
    click.echo("".join(lines).rstrip("\n"))
    raise SystemExit(1)


@prompt_group.command("status")
@_PROMPT_DIR_OPTION
def prompt_status(directory: str) -> None:
    """Report which source the effective workspace prompt comes from, and how large it is.

    Exits 1 when a source is configured but resolves to nothing — the shape a rollback to a
    wheel that does not read the configured knob produces."""
    config = _prompt_config(directory)
    resolved, source = _effective_prompt(config)
    _echo_prompt_status(source, resolved)
    if source == _OVERRIDE_SOURCE:
        click.echo("the override wins over every config knob until it is cleared: DELETE /api/workspace-prompt")
        return
    if source != "none" and not resolved.strip():
        raise click.ClickException(f"{source} is configured but the workspace prompt resolves to nothing")


def _echo_prompt_status(source: str, prompt: str) -> None:
    click.echo(f"workspace prompt: {len(prompt)} characters, from {source}")


def _effective_prompt(config: RunnerConfig) -> tuple[str, str]:
    """The prompt a spawn would read, and the lane it came from — the one definition the
    `prompt` verbs share, mirroring `SpawnPlan._render`'s override-first precedence."""
    override = _stored_override(config)
    if override is not None:
        return override, _OVERRIDE_SOURCE
    try:
        return config.resolved_workspace_prompt(), _configured_source(config)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def _configured_source(config: RunnerConfig) -> str:
    """Which config knob layer 2 resolves from, in the precedence `resolved_workspace_prompt` applies."""
    if config.workspace_prompt_package:
        return f'package "{config.workspace_prompt_package}"'
    if config.workspace_prompt_file:
        return f"file {config.workspace_prompt_file}"
    return "inline workspace_prompt" if config.workspace_prompt else "none"


def _stored_override(config: RunnerConfig) -> str | None:
    """The store's runtime override, or ``None``. A read-only query, so a live daemon is no bar."""
    try:
        store = SqlAlchemyRunnerStore(create_engine_from_url(config.db_url))
        return store.workspace_prompt_override(config.workspace_id)
    except SQLAlchemyError as exc:
        raise click.ClickException(f"could not read the runner store at {config.db_url}: {exc}") from exc


def _repoint_config(root: Path, *, file_path: str) -> None:
    """Point the top-level prompt knobs at an installed copy, leaving the rest of the file alone.

    A targeted line rewrite: regenerating the config would drop every comment and table an
    operator added. A knob the file predates is inserted beside its siblings rather than at the
    region boundary, where it would split a table's comment block off from its header."""
    path = root / CONFIG_FILENAME
    lines = path.read_text().splitlines(keepends=True)
    knobs = {"workspace_prompt_file": json.dumps(file_path), "workspace_prompt_package": '""'}
    end = next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    at = end
    for i, line in enumerate(lines[:end]):
        key = line.split("=", 1)[0].strip()
        if key.startswith("workspace_prompt"):
            at = i + 1
        if key in knobs:
            lines[i] = f"{key} = {knobs.pop(key)}\n"
    if knobs:
        _insert_knobs(lines, at=at, boundary=end, knobs=knobs)
    path.write_text("".join(lines))


def _insert_knobs(lines: list[str], *, at: int, boundary: int, knobs: dict[str, str]) -> None:
    """Splice absent knobs in, rewinding off a table's own comment block and terminating the line
    above — an unterminated last line would otherwise concatenate into invalid TOML."""
    if at == boundary:
        while at > 0 and (lines[at - 1].lstrip().startswith("#") or not lines[at - 1].strip()):
            at -= 1
    if at > 0 and not lines[at - 1].endswith("\n"):
        lines[at - 1] += "\n"
    lines[at:at] = [f"{key} = {value}\n" for key, value in knobs.items()]


@runner.group("transcript")
def transcript_group() -> None:
    """Operator: maintenance over this runner's own transcript lane (blizzard#250)."""


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


@runner.command()
@click.argument("prompt")
@click.option("--options", default=None, help="Pipe-separated answer options.")
def ask(prompt: str, options: str | None) -> None:
    """Worker: ask-and-exit — the ask is a durable runner-store fact before this returns."""
    worker = WorkerCall.of("ask")
    body: dict[str, object] = {"question": prompt}
    if options:
        body["options"] = [o for o in options.split("|") if o]
    resp = worker.post(worker.leased("asks"), failure="could not record the question", json_body=body)
    click.echo(resp.json().get("question_id", ""))


@runner.group("artifact")
def artifact_group() -> None:
    """Worker: read node-step and graph artifacts; write this node-step's own (issue #127). The
    lease binding is ambient: every verb acts on the worker's own lease, resolved from the spawn
    environment, so none takes a flag by which a worker could name another chunk. ``--scope``
    picks between node scope and the graph mint's baked-in declarations. ``create`` *stages* a
    submission, published on completion (#169)."""


@dataclass(frozen=True)
class ArtifactEntry:
    """One ``list``-view entry (issue #169) — every field but ``content``, which collapses to
    its ``bytes`` length (``None`` when the artifact carries none, i.e. ``git_commit``).
    Carries ``scope`` (node/graph) like every other field."""

    artifact: dict

    @property
    def summary(self) -> dict:
        content = self.artifact.get("content")
        summary = {k: v for k, v in self.artifact.items() if k != "content"}
        summary["bytes"] = len(content.encode("utf-8")) if content is not None else None
        return summary


def _refuse_graph_scope(verb: str, scope: str | None) -> None:
    """``create``/``commit``/``staged`` are node-scope only: a graph's declarations are baked at
    mint and read-only. Refusing here states that domain fact to a worker parsing stderr mid-turn
    (which scopes each verb serves: ``blizzard-context:/standards/worker-nodes.md``)."""
    if scope == ArtifactScope.GRAPH.value:
        raise click.ClickException(
            f"artifact {verb}: graph scope is read-only — a graph's declarations are baked at mint"
        )


_SCOPE_CHOICE = click.Choice([s.value for s in ArtifactScope])


@artifact_group.command("list")
@click.option(
    "--content",
    "content",
    is_flag=True,
    default=False,
    help="Include each artifact's full content instead of just its byte length.",
)
@click.option(
    "--scope",
    "scope",
    type=_SCOPE_CHOICE,
    default=None,
    help="Filter to one scope — `node` (this node-step's own artifacts) or `graph` (the graph "
    "mint's baked-in declarations). Omitted reads both.",
)
def artifact_list(content: bool, scope: str | None) -> None:
    """Worker: list this node-step's artifacts as kind-discriminated JSON, resolved latest-by-epoch,
    plus the graph mint's own baked-in declarations — ``--scope`` narrows to one.

    Content is elided by default (issue #169), since inlining every upstream asset's full text
    has overflowed tool output; ``--content`` restores it."""
    worker = WorkerCall.of("artifact list")
    resp = worker.get(
        worker.leased("artifacts"),
        failure="could not read the artifacts",
        params={"scope": scope} if scope else None,
    )
    if content:
        click.echo(resp.text)
        return
    click.echo(json.dumps([ArtifactEntry(a).summary for a in resp.json()]))


@artifact_group.command("get")
@click.argument("name")
@click.option(
    "--node",
    "node",
    default=None,
    help="The producing node's name, to disambiguate a NAME more than one node emits. A "
    "graph declaration has no producing node, so this narrows to node scope on its own — "
    "pairing it with `--scope graph` is a contradiction and is refused.",
)
@click.option(
    "--scope",
    "scope",
    type=_SCOPE_CHOICE,
    default=None,
    help="Resolve NAME from one scope only — `node` or `graph`. Omitted searches both, and a "
    "NAME present in both is ambiguous the same as several producing nodes — unless `--node` "
    "settles it.",
)
@click.option(
    "--content",
    "content",
    is_flag=True,
    default=False,
    help="Print the raw asset text to stdout instead of JSON (errors on a git-commit artifact).",
)
def artifact_get(name: str, node: str | None, scope: str | None, content: bool) -> None:
    """Worker: read one artifact by NAME — a ``produces:`` name (node scope) or a baked-in graph
    declaration (graph scope); unknown is a ``404``, and more than one candidate — several
    upstream nodes (issue #169), or both scopes at once — exits non-zero naming them.
    ``--content`` prints raw asset text, and errors on the ``git_commit`` kind, which carries
    none. NAME is percent-encoded (issue #233)."""
    worker = WorkerCall.of("artifact get")
    params: dict[str, str] = {}
    if node:
        params["node"] = node
    if scope:
        params["scope"] = scope
    resp = worker.get(
        worker.leased(f"artifacts/{quote(name, safe='/')}"),
        failure=f"could not read {name!r}",
        params=params or None,
    )
    if not content:
        click.echo(resp.text)
        return
    artifact = resp.json()
    if artifact.get("kind") == ArtifactKind.GIT_COMMIT:
        raise click.ClickException(
            f"artifact get: {name!r} is a git-commit artifact — it has no content (drop --content to read its ref)"
        )
    # Raw, un-decorated: the asset text as stored, no added trailing newline.
    click.echo(artifact.get("content") or "", nl=False)


@artifact_group.command("create")
@click.option("--name", required=True, help="The `produces:` name this content is submitted for.")
@click.option(
    "--scope",
    "scope",
    type=_SCOPE_CHOICE,
    default=None,
    help="Always `node` — `graph` is refused, since a graph-mint declaration is baked in at mint and read-only.",
)
def artifact_create(name: str, scope: str | None) -> None:
    """Worker: durably submit an asset artifact for a ``produces:`` NAME (content on stdin), node
    scope only — ``--scope graph`` is refused, since a graph-mint declaration is read-only.
    A submission *stages* the content, published into the envelope only on completion (issue #169)
    — read it back with ``artifact staged``. Empty stdin and any rejection exit non-zero rather
    than silently losing the submission."""
    _refuse_graph_scope("create", scope)
    worker = WorkerCall.of("artifact create")
    content = click.get_text_stream("stdin").read()
    if not content:
        raise click.ClickException(
            "artifact create: empty stdin — refusing to submit an empty artifact "
            "(any previously staged submission for this name is untouched)"
        )
    resp = worker.post(
        worker.leased("attachments"),
        failure=f"could not record {name!r}",
        json_body={"name": name, "content": content},
    )
    body = resp.json()
    click.echo(f"recorded {body.get('name', name)!r} ({body.get('bytes', len(content.encode('utf-8')))} bytes)")


@artifact_group.command("staged")
@click.option(
    "--content",
    "content",
    is_flag=True,
    default=False,
    help="Include each staged submission's full content instead of just its byte length.",
)
@click.option(
    "--scope",
    "scope",
    type=_SCOPE_CHOICE,
    default=None,
    help="Always `node` — `graph` is refused, since graph scope has no staged submissions by construction.",
)
def artifact_staged(content: bool, scope: str | None) -> None:
    """Worker: list this node-step's own staged (not-yet-published) submissions, node scope only
    — ``--scope graph`` is refused, a graph declaration never being staged. Read straight
    off the runner's own ``attachments`` record rather than the hub envelope (issue #169), so a
    fresh ``artifact create`` shows up here immediately; ``--content`` gives the full text."""
    _refuse_graph_scope("staged", scope)
    worker = WorkerCall.of("artifact staged")
    resp = worker.get(worker.leased("attachments"), failure="could not read the staged artifacts")
    if content:
        click.echo(resp.text)
        return
    staged = resp.json()
    click.echo(json.dumps([{"name": a["name"], "bytes": len(a["content"].encode("utf-8"))} for a in staged]))


@dataclass(frozen=True)
class SessionLabel:
    """A parked session's identity as a trailing clause — ``"  session=code (opus, high)"``.

    Empty when the escalation carries none of the three (issue #144), so a bare line reads as
    "not recorded" rather than inventing one."""

    escalation: dict

    @property
    def text(self) -> str:
        pool = self.escalation.get("session_name")
        config = ", ".join(str(v) for v in (self.escalation.get("model"), self.escalation.get("effort")) if v)
        if not pool and not config:
            return ""
        if not pool:
            return f"  session=({config})"
        return f"  session={pool}" + (f" ({config})" if config else "")


@artifact_group.command("commit")
@click.option(
    "--env",
    "environment_id",
    default=None,
    help="The leased environment the repo worktree lives in. Optional while a chunk "
    "holds exactly one environment (it is inferred); required once it holds several, "
    "since the same repo has a worktree in each.",
)
@click.option(
    "--repo",
    required=True,
    help="The repo's name in the leased env's manifest (not an `owner/name` slug or "
    "URL) — the runner looks this up in the environment's repo manifest to find both "
    "the worktree and the origin to verify against. A name the manifest does not list "
    "is rejected outright, naming the repos that are.",
)
@click.option("--branch", required=True, help="The branch the commit was pushed to.")
@click.option(
    "--commit",
    "commit_sha",
    required=True,
    help="The FULL commit sha (`git rev-parse HEAD`), not an abbreviated form — verify "
    "compares it byte-exact against the forge's full sha.",
)
@click.option(
    "--scope",
    "scope",
    type=_SCOPE_CHOICE,
    default=None,
    help="Always `node` — `graph` is refused, since a graph-mint declaration is baked in at mint and read-only.",
)
def artifact_commit(environment_id: str | None, repo: str, branch: str, commit_sha: str, scope: str | None) -> None:
    """Worker: durably declare a git-commit artifact for REPO (issue #143). Carries the ``git_commit``
    kind only — an asset is declared through ``artifact create``. Node scope only — ``--scope graph``
    is refused. Deliberately no ``--forge``: the origin comes from the environment's repo
    manifest (pinned by tests/test_runner_artifact_commit_cli.py::test_commit_verb_has_no_forge_flag)."""
    _refuse_graph_scope("commit", scope)
    worker = WorkerCall.of("artifact commit")
    body: dict[str, str] = {"repo": repo, "branch": branch, "commit": commit_sha}
    if environment_id is not None:
        body["environment_id"] = environment_id
    worker.post(
        worker.leased("git-commits"),
        failure=f"could not record {repo!r}",
        rejected=f"{repo!r} rejected",
        json_body=body,
    )


@runner.group("chunk")
def chunk_group() -> None:
    """Worker: read facts about the chunk this node-step belongs to.

    The lease binding is ambient, like ``artifact``: every verb in this group acts on the worker's
    own lease, resolved from the spawn environment, so none takes a flag by which a worker could
    name another chunk."""


@chunk_group.command("history")
def chunk_history() -> None:
    """Worker: read this chunk's own transition history as kind-discriminated JSON (issue #237) — the
    merged, oldest-first timeline, one row per accepted transition, cross-graph migration, or delivery
    bounce, each carrying its own ``kind``. The in-flight node-step this call is part of is not
    there yet: a transition is recorded only once an attempt completes."""
    worker = WorkerCall.of("chunk history")
    resp = worker.get(worker.leased("history"), failure="could not read the history")
    click.echo(resp.text)


@runner.command(hidden=True)
@click.option("--name", required=True, help="The `produces:` name this content is submitted for.")
@click.pass_context
def attach(ctx: click.Context, name: str) -> None:
    """Deprecated alias for ``blizzard runner artifact create`` (issue #127).

    Kept working, hidden from ``--help``: it warns on stderr and delegates with identical behavior."""
    click.echo(
        "warning: `blizzard runner attach` is deprecated — use `blizzard runner artifact create`",
        err=True,
    )
    ctx.invoke(artifact_create, name=name)


@runner.command("work-items")
@click.argument("chunk_id")
def work_items(chunk_id: str) -> None:
    """Worker: pass-through read of a chunk's work items (runner -> hub -> vendor).

    The items print as JSON, one entry per pointer."""
    worker = WorkerCall.of("work-items", lease=False)
    resp = worker.get(f"/api/chunks/{chunk_id}/work-items", failure="could not read the work item")
    click.echo(resp.text)


@runner.command("pm-items", hidden=True)
@click.argument("chunk_id")
@click.pass_context
def pm_items(ctx: click.Context, chunk_id: str) -> None:
    """Deprecated alias for ``blizzard runner work-items`` (issue #55).

    Kept working, hidden from ``--help``: a node's prompt is inlined into the store at mint and
    immutable thereafter, so every already-minted graph names this verb forever (pinned by
    tests/test_pin_runner_misc.py::test_the_deprecated_pm_items_cli_alias_still_reads_the_work_item)."""
    click.echo(
        "warning: `blizzard runner pm-items` is deprecated — use `blizzard runner work-items`",
        err=True,
    )
    ctx.invoke(work_items, chunk_id=chunk_id)


@runner.command()
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option(
    "--runner-url",
    "runner_url",
    default=None,
    envvar=ENV_LOCAL_API_URL,
    help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
)
def status(directory: str, runner_url: str | None) -> None:
    """The machine-local view: capacities, held environments, open asks, escalations, open takeovers
    (issue #51). Every section is this runner's own local read, so the view renders fully with the
    hub unreachable; hub reachability is itself reported, not assumed."""
    with RunnerDaemon.reach("status", directory, runner_url) as daemon:
        view = daemon.get("/api/runner").json()
        leases_resp = daemon.get("/api/leases")
        envs_resp = daemon.get("/api/environments")
        asks_resp = daemon.get("/api/asks", params={"open": "true"})
        escalations_resp = daemon.get("/api/escalations")
        takeovers_resp = daemon.get("/api/takeovers")

    click.echo(f"runner {view['runner_id']}  workspace={view['workspace_id']}")
    pause = view["pause"]
    brakes = [name for name, on in (("local", pause["local"]), ("hub", pause["hub"])) if on]
    brake_state = f"paused [{'+'.join(brakes)}]" if pause["effective"] else "running"
    click.echo(f"  {brake_state}")
    cap = view["capacities"]
    click.echo(f"  capacity: {cap['used']}/{cap['max_agents']} used, {cap['free']} free")
    hub = view["hub"]
    reachability = "reachable" if hub["reachable"] else "unreachable"
    contact = hub["last_contact_at"] or "never"
    click.echo(f"  hub: {reachability} (last contact {contact}), {hub['buffer_depth']} fact(s) buffered")
    click.echo(f"  last tick: {view['last_tick_at'] or 'never'}")

    leases = [lease for lease in leases_resp.json().get("items", []) if lease.get("state") != "closed"]
    click.echo(f"\nleases ({len(leases)}):")
    for lease in leases:
        click.echo(f"  {lease['lease_id']}  {lease['state']:<12} chunk={lease['chunk_id']} node={lease['node_name']}")

    # `GET /api/environments` carries the full configured pool (issue #106); this section
    # is the *held*-environments view, so unused pool slots (chunk_id null) are filtered out.
    envs = [env for env in envs_resp.json().get("items", []) if env.get("chunk_id") is not None]
    click.echo(f"\nheld environments ({len(envs)}):")
    for env in envs:
        click.echo(f"  {env['environment_id']}  chunk={env['chunk_id']}  held since {env['held_since']}")

    asks = asks_resp.json().get("items", [])
    click.echo(f"\nopen asks ({len(asks)}):")
    for ask in asks:
        opts = f"  [{'|'.join(ask.get('options') or [])}]" if ask.get("options") else ""
        click.echo(f"  {ask['question_id']}  (chunk {ask['chunk_id']}): {ask['question']}{opts}")

    escalations = escalations_resp.json().get("items", [])
    click.echo(f"\nescalations ({len(escalations)}):")
    for esc in escalations:
        click.echo(
            f"  chunk {esc['chunk_id']}  node={esc['node_id']}  since {esc['closed_at']}{SessionLabel(esc).text}"
        )
        click.echo(f"    resume: {esc['resume_command']}")

    takeovers = takeovers_resp.json().get("items", [])
    click.echo(f"\nopen takeovers ({len(takeovers)}):")
    for tko in takeovers:
        click.echo(f"  chunk {tko['chunk_id']}  takeover={tko['takeover_id']}  held since {tko['held_since']}")


@runner.command()
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option(
    "--runner-url",
    "runner_url",
    default=None,
    envvar=ENV_LOCAL_API_URL,
    help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
)
@click.option("--by", "by", default="operator", help="Who is pausing (recorded on the fact).")
def pause(directory: str, runner_url: str | None, by: str) -> None:
    """Declarative control: pause this runner — it starts no new workers (issue #45). This runner's
    **own** brake, a pure client of its local API, so it works with the hub unreachable: it blocks
    every spawn site and defers both the kill of a stalled worker and escalation at an exhausted retry
    budget. No retry is consumed, and a live worker is left alone — this is not a drain. Distinct from
    the hub's brake, and each is cleared where it was set."""
    _set_local_paused(paused=True, by=by, directory=directory, runner_url=runner_url)


@runner.command()
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option(
    "--runner-url",
    "runner_url",
    default=None,
    envvar=ENV_LOCAL_API_URL,
    help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
)
@click.option("--by", "by", default="operator", help="Who is starting it (recorded on the fact).")
def start(directory: str, runner_url: str | None, by: str) -> None:
    """Declarative control: clear this runner's own pause brake — it resumes spawning (issue #45).

    The counterpart to ``blizzard runner pause``, and local in the same way. It clears only
    the local brake: a runner also paused at the hub stays paused until ``blizzard hub
    runner resume <runner_id>`` clears that one too."""
    _set_local_paused(paused=False, by=by, directory=directory, runner_url=runner_url)


@runner.command()
@click.argument("chunk_id")
@click.option("--force", is_flag=True, default=False, help="Supersede a live worker attempt instead of refusing.")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option(
    "--runner-url",
    "runner_url",
    default=None,
    envvar=ENV_LOCAL_API_URL,
    help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
)
def takeover(chunk_id: str, force: bool, directory: str, runner_url: str | None) -> None:
    """Take over a parked chunk: exec the interactive resume command in this terminal (issue #52). The
    takeover fact is recorded before anything else runs, so no loop step can respawn or judge the
    session while it is open; the lease token travels only in the response body and the exec, never
    printed. ``--force`` supersedes a live worker attempt instead of refusing. The end-PATCH runs in a
    ``finally`` around the child, so a stranded open takeover cannot outlive an interrupted session."""
    with RunnerDaemon.reach("takeover", directory, runner_url) as daemon:
        resp = daemon.send("post", f"/api/chunks/{chunk_id}/takeovers", json_body={"force": force})
        if resp.status_code == 409:
            raise click.ClickException(f"takeover: {resp.json().get('detail', 'chunk is not takeable')}")
        resp.raise_for_status()
        view = resp.json()
        click.echo(f"taking over chunk {chunk_id} in {view['workdir']}")
        try:
            # The takeover env (issue #258), layered over the terminal env: the forwarded
            # vars deliberately WIN over the terminal's own, and carry the lease token.
            child_env = {**os.environ, **view.get("env", {})}
            exit_code = subprocess.call(view["command"], shell=True, cwd=view["workdir"], env=child_env)
        finally:
            daemon.patch(f"/api/chunks/{chunk_id}/takeovers/{view['takeover_id']}")
    if exit_code != 0:
        raise SystemExit(exit_code)


@runner.command()
@click.argument("chunk_id")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option(
    "--runner-url",
    "runner_url",
    default=None,
    envvar=ENV_LOCAL_API_URL,
    help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
)
def requeue(chunk_id: str, directory: str, runner_url: str | None) -> None:
    """Hand a needs_human chunk back to the fleet: a fresh attempt at its current node (issue #53).
    Appends the fact that clears the chunk's local needs_human hold; the next FILL spawns a fresh
    attempt — new session, new lease, fresh epoch — at the current node. The route is never released
    and the chunk never re-enters the hub's queue. Refused ``409`` while its takeover is still open,
    or while it is not parked needs_human."""
    with RunnerDaemon.reach("requeue", directory, runner_url) as daemon:
        resp = daemon.send("post", f"/api/chunks/{chunk_id}/requeues")
        if resp.status_code == 409:
            raise click.ClickException(f"requeue: {resp.json().get('detail', 'chunk is not requeueable')}")
        resp.raise_for_status()
    click.echo(f"requeued chunk {chunk_id} — a fresh attempt will spawn at its current node")


@runner.command()
@click.argument("coding_harness")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option(
    "--runner-url",
    "runner_url",
    default=None,
    envvar=ENV_LOCAL_API_URL,
    help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
)
def selftest(coding_harness: str, directory: str, runner_url: str | None) -> None:
    """Adapter-drift canary before an unattended period (issue #54): exercises CODING_HARNESS against a
    throwaway scratch repo — spawn with a pre-assigned session id, a trivial edit+commit, verdict
    elicitation, an automated follow-up resume, and resume-command composition — touching no chunk,
    lease, environment, or hub. Posts the run, polls it, prints each check, exits non-zero on failure."""
    with RunnerDaemon.reach("selftest", directory, runner_url) as daemon:
        resp = daemon.send("post", "/api/selftests", json_body={"harness": coding_harness})
        if resp.status_code == 422:
            raise click.ClickException(resp.json().get("detail", "unknown coding harness"))
        resp.raise_for_status()
        run = resp.json()
        deadline = time.monotonic() + _SELFTEST_POLL_TIMEOUT
        while run["status"] == "running":
            if time.monotonic() > deadline:
                raise click.ClickException(
                    f"selftest {run['id']} did not finish within {_SELFTEST_POLL_TIMEOUT:g}s — the runner may be wedged"
                )
            time.sleep(_SELFTEST_POLL_INTERVAL)
            run = daemon.get(f"/api/selftests/{run['id']}").json()

    for check in run["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        click.echo(f"[{mark}] {check['name']}: {check['detail']}")
    if run["status"] != "passed":
        if run.get("error"):
            click.echo(f"selftest error: {run['error']}", err=True)
        click.echo(f"selftest {run['id']} FAILED for {coding_harness}", err=True)
        raise click.exceptions.Exit(1)
    click.echo(f"selftest {run['id']} passed for {coding_harness}")

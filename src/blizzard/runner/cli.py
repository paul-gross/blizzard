"""``blizzard runner <cmd>`` — the machine-local surface.

Client verbs are pure clients of the runner's local API; ``host`` *becomes* the runner daemon.
Worker-hook verbs take their identity from the spawn-injected environment and pass no identity
arguments."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import types
from pathlib import Path
from urllib.parse import quote

import click
import httpx
import uvicorn

from blizzard.cli.host_directory import resolve_host_directory
from blizzard.cli.param_rank import source_rank
from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli_worker import WorkerCall
from blizzard.runner.config import ConfigError, RunnerConfig, socket_path_for
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.listeners import ListenerError, bind_listeners, unlink_socket
from blizzard.runner.loop.build import (
    PeriodicDriver,
    mark_crash_resume_intents_on_startup,
    mark_resume_intents_on_shutdown,
    run_single_tick,
)
from blizzard.runner.runtime import ensure_current_revision, init_environment, migrate, migration_runner

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
# A machine-local round trip (issue #43), so a hook-scale budget rather than the hub-client one.
_LOCAL_CLIENT_TIMEOUT = 5.0
# Each `selftest` poll is a machine-local read of already-computed state, so a short interval is free.
_SELFTEST_POLL_INTERVAL = 0.2
# A CLI-side backstop above the server's own authoritative run budget, so the CLI never spins forever
# against a runner that cannot reach that code.
_SELFTEST_POLL_TIMEOUT = 600.0


# Ranked by where each value came from (`param_rank.py`) because `--dir` always *has* a value: an
# explicit flag beats an ambient variable, and only a genuine tie on the command line is ambiguous.
def _local_api_client(directory: str, runner_url: str | None) -> tuple[httpx.Client, str]:
    """A client of the runner's local API, over the socket or TCP — never the store, never the hub."""
    ctx = click.get_current_context()
    dir_rank = source_rank(ctx.get_parameter_source("directory"))
    url_rank = source_rank(ctx.get_parameter_source("runner_url")) if runner_url is not None else -1

    if dir_rank == 2 and url_rank == 2:
        raise click.UsageError(
            "--dir and --runner-url are mutually exclusive: --dir names the socket, --runner-url TCP"
        )
    if url_rank > dir_rank and runner_url is not None:
        return httpx.Client(base_url=runner_url, timeout=_LOCAL_CLIENT_TIMEOUT), runner_url

    sock = socket_path_for(Path(directory))
    if not sock.exists():
        # No degraded read path — an absent socket is a daemon-not-running diagnostic,
        # never a reason to fall back to reading the store.
        raise click.ClickException(
            f"no runner daemon is serving at {sock} — start one with `blizzard runner host --dir {directory}`"
        )
    # The base_url host is a placeholder: the UDS transport decides where the bytes go.
    transport = httpx.HTTPTransport(uds=str(sock))
    return httpx.Client(transport=transport, base_url="http://runner", timeout=_LOCAL_CLIENT_TIMEOUT), str(sock)


def _set_local_paused(*, paused: bool, by: str, directory: str, runner_url: str | None) -> None:
    """PATCH the runner singleton's own pause brake — the declarative pattern applied locally."""
    client, where = _local_api_client(directory, runner_url)
    verb = "pause" if paused else "start"
    try:
        with client:
            resp = client.patch("/api/runner", json={"paused": paused, "by": by})
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise click.ClickException(f"{verb}: could not reach the runner at {where} ({exc})") from exc
    view = resp.json()
    if paused:
        click.echo(f"runner {view['runner_id']} is now locally paused — it starts no new workers")
        if view.get("hub_paused"):
            click.echo("note: it is also paused at the hub — `blizzard hub resume` clears that one")
        return
    click.echo(f"runner {view['runner_id']} is no longer locally paused")
    if view.get("hub_paused"):
        click.echo("note: it stays paused at the hub — clear that with `blizzard hub resume`")


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
    config = init_environment(Path(directory))
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
    directory = resolve_host_directory(directory, dir_option)
    try:
        config = RunnerConfig.load(Path(directory), host=host_, port=port)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        ensure_current_revision(config)
    except RevisionMismatchError as exc:
        raise click.ClickException(str(exc)) from exc
    app = build_hosted_app(config)
    interval = float(os.environ.get(ENV_TICK_SECONDS, DEFAULT_TICK_SECONDS))
    # `PeriodicDriver` resolves its prompt files on this thread, not in the loop thread: a
    # configured-but-missing prompt raises here, before any socket binds.
    try:
        driver = PeriodicDriver(config, interval_seconds=interval)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    # Two doors onto the one app (issue #43), bound up front so a clash fails startup loudly and
    # served by the single `Server` below, which keeps the shutdown path on one frame.
    try:
        sockets = bind_listeners(config)
    except ListenerError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"serving blizzard-runner on {config.host}:{config.port} and {config.socket_path} (loop tick {interval}s)"
    )

    # SIGTERM must drain the server (set `should_exit`) rather than hard-exit, or `run()` never returns
    # and the `finally` resume-marking below is never reached (tests/crash/test_kill9_sweep.py).
    server = uvicorn.Server(uvicorn.Config(app, host=config.host, port=config.port))

    def _drain(_signum: int, _frame: types.FrameType | None) -> None:
        server.should_exit = True

    signal.signal(signal.SIGTERM, _drain)
    signal.signal(signal.SIGINT, _drain)
    if hasattr(server, "install_signal_handlers"):
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

    # Ungraceful-restart recovery (#13): a `kill -9` never ran the graceful shutdown marker below, so
    # sessions killed mid-work are marked here for the same startup RESUME the first tick runs.
    resumable = mark_crash_resume_intents_on_startup(config)
    if resumable:
        click.echo(f"marked {resumable} crash-interrupted lease(s) for restart-resume")

    driver.start()  # startup recovery is REAP running first inside the tick
    try:
        server.run(sockets=sockets)
    finally:
        # Stop the loop first so no in-flight tick races the marking: `stop()` blocks on the tick
        # thread, so the loop is quiescent before every in-flight lease is marked.
        driver.stop()
        marked = mark_resume_intents_on_shutdown(config)
        if marked:
            click.echo(f"marked {marked} in-flight lease(s) for restart-resume")
        # uvicorn closes a pre-bound socket but does not unlink its file; leaving it would
        # make the next start take the stale-corpse path in `bind_listeners` for nothing.
        unlink_socket(config.socket_path)


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
    run_single_tick(config)
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

    A pure client of the runner's local API, taking its lease and runner URL from the spawn
    environment, so no arguments. Fails **soft**: a hook must never break the worker's tool call, so a
    missing identity or an unreachable runner is reported to stderr and this still exits 0."""
    worker = WorkerCall.hook("heartbeat")
    if worker is not None:
        worker.soft_post(
            "/api/heartbeat", failure="could not reach the runner", json_body={"lease_id": worker.lease_id}
        )


@runner.command("session-end")
def session_end() -> None:
    """Worker hook: record the session's exit (identity from the environment).

    A pure client of the runner's local API, taking its identity from the spawn environment. The
    recorded fact is the worker's "declared done" signal. Fails **soft**, like the heartbeat: a hook
    must never break the worker's exit, so a failure is reported to stderr and this still exits 0."""
    worker = WorkerCall.hook("session-end")
    if worker is not None:
        worker.soft_post(worker.leased("session-end"), failure="could not reach the runner")


@runner.command()
@click.argument("prompt")
@click.option("--options", default=None, help="Pipe-separated answer options.")
def ask(prompt: str, options: str | None) -> None:
    """Worker: ask-and-exit; the ask fact is durable before the worker exits.

    A pure client of the runner's local API, taking its identity from the spawn environment, so no
    identity arguments. The ask is a durable runner-store fact before this returns."""
    worker = WorkerCall.of("ask")
    body: dict[str, object] = {"question": prompt}
    if options:
        body["options"] = [o for o in options.split("|") if o]
    resp = worker.post(worker.leased("asks"), failure="could not record the question", json_body=body)
    click.echo(resp.json().get("question_id", ""))


@runner.group("artifact")
def artifact_group() -> None:
    """Worker: read and write this node-step's own artifacts (issue #127).

    Scope is ambient: every verb acts on the worker's own lease, resolved from the spawn environment,
    so none takes a flag by which a worker could name another chunk. ``create`` *stages* a submission —
    durable at once, visible only via ``staged``, published into the envelope on completion (#169)."""


def _artifact_summary(artifact: dict) -> dict:
    """One ``list``-view entry: every field but ``content``, which collapses to its
    ``bytes`` length (``None`` when the artifact carries no content, i.e. ``git_commit``)
    — issue #169's fix for a full-content ``list`` overflowing tool output."""
    content = artifact.get("content")
    summary = {k: v for k, v in artifact.items() if k != "content"}
    summary["bytes"] = len(content.encode("utf-8")) if content is not None else None
    return summary


@artifact_group.command("list")
@click.option(
    "--content",
    "content",
    is_flag=True,
    default=False,
    help="Include each artifact's full content instead of just its byte length.",
)
def artifact_list(content: bool) -> None:
    """Worker: list this node-step's artifacts as kind-discriminated JSON, resolved latest-by-epoch.

    A pure client of the runner's local API, which proxies to the hub as the runner principal — the
    worker holds no hub credential. Content is elided by default (issue #169), since inlining every
    upstream asset's full text has overflowed tool output; ``--content`` restores it."""
    worker = WorkerCall.of("artifact list")
    resp = worker.get(worker.leased("artifacts"), failure="could not read the artifacts")
    if content:
        click.echo(resp.text)
        return
    click.echo(json.dumps([_artifact_summary(a) for a in resp.json()]))


@artifact_group.command("get")
@click.argument("name")
@click.option(
    "--node",
    "node",
    default=None,
    help="The producing node's name, to disambiguate a NAME more than one node emits.",
)
@click.option(
    "--content",
    "content",
    is_flag=True,
    default=False,
    help="Print the raw asset text to stdout instead of JSON (errors on a git-commit artifact).",
)
def artifact_get(name: str, node: str | None, content: bool) -> None:
    """Worker: read one artifact by NAME — the same lease-scoped, hub-proxied read as ``list``, narrowed
    to one ``produces:`` name; an unknown name is a ``404``, and a name several upstream nodes emit
    (issue #169) exits non-zero naming them rather than picking arbitrarily — ``--node`` disambiguates.
    ``--content`` prints raw asset text instead, and errors on the ``git_commit`` kind, which carries
    none. NAME is percent-encoded (issue #233), so a slash-containing name round-trips like any other."""
    worker = WorkerCall.of("artifact get")
    resp = worker.get(
        worker.leased(f"artifacts/{quote(name, safe='/')}"),
        failure=f"could not read {name!r}",
        params={"node": node} if node else None,
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
def artifact_create(name: str) -> None:
    """Worker: durably submit an asset artifact for a ``produces:`` NAME (content on stdin), authorized
    by the lease token in the spawn environment. Writes the ``asset`` kind only. A submission *stages*
    the content for this node-step, published into the envelope only on completion (issue #169) — read
    it back with ``artifact staged``. Empty stdin and any rejection exit non-zero rather than silently
    losing the submission."""
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
def artifact_staged(content: bool) -> None:
    """Worker: list this node-step's own staged (not-yet-published) submissions.

    Read straight off the runner's own ``attachments`` record rather than the hub envelope (issue
    #169), so a fresh ``artifact create`` shows up here immediately. Content is elided by default,
    same as ``list``; ``--content`` gives the full text."""
    worker = WorkerCall.of("artifact staged")
    resp = worker.get(worker.leased("attachments"), failure="could not read the staged artifacts")
    if content:
        click.echo(resp.text)
        return
    staged = resp.json()
    click.echo(json.dumps([{"name": a["name"], "bytes": len(a["content"].encode("utf-8"))} for a in staged]))


def _session_label(escalation: dict) -> str:
    """The parked session's identity, as a trailing clause — ``"  session=code (opus, high)"``.

    Empty when the escalation carries none of the three (issue #144): neither a bare-vocabulary session
    nor one predating the stamps invents a value — a bare line reads as "not recorded"."""
    pool = escalation.get("session_name")
    config = ", ".join(str(v) for v in (escalation.get("model"), escalation.get("effort")) if v)
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
def artifact_commit(environment_id: str | None, repo: str, branch: str, commit_sha: str) -> None:
    """Worker: durably declare a git-commit artifact for REPO (issue #143), authorized by the lease
    token in the spawn environment. Carries the ``git_commit`` kind only — an asset is declared through
    ``artifact create``. There is deliberately no ``--forge``: the origin a declaration is verified
    against comes from the environment's repo manifest, so a worker cannot supply the wrong one (pinned
    by tests/test_runner_artifact_commit_cli.py::test_commit_verb_has_no_forge_flag)."""
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

    Scope is ambient, like ``artifact``: every verb in this group acts on the worker's own lease,
    resolved from the spawn environment, so none takes a flag by which a worker could name another
    chunk."""


@chunk_group.command("history")
def chunk_history() -> None:
    """Worker: read this chunk's own transition history as kind-discriminated JSON (issue #237) — the
    merged, oldest-first timeline, one row per accepted transition, cross-graph migration, or delivery
    bounce, each carrying its own ``kind``. A pure client of the runner's local API, which proxies to
    the hub as the runner principal. The in-flight node-step this call is part of is not there yet: a
    transition is recorded only once an attempt completes."""
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

    A pure client of the runner's local API, whose proxy route forwards to the hub — the worker never
    talks to the hub or the work source directly. The runner URL is inherited from the spawn
    environment, so no identity argument; the items print as JSON, one entry per pointer."""
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
    (issue #51). A pure client of the runner's local API — the same door ``pause``/``start`` use — so
    every section is this runner's own local read and the view renders fully with the hub unreachable;
    hub reachability is itself reported, not assumed. No store access, no hub call."""
    client, where = _local_api_client(directory, runner_url)
    try:
        with client:
            runner_resp = client.get("/api/runner")
            runner_resp.raise_for_status()
            leases_resp = client.get("/api/leases")
            leases_resp.raise_for_status()
            envs_resp = client.get("/api/environments")
            envs_resp.raise_for_status()
            asks_resp = client.get("/api/asks", params={"open": "true"})
            asks_resp.raise_for_status()
            escalations_resp = client.get("/api/escalations")
            escalations_resp.raise_for_status()
            takeovers_resp = client.get("/api/takeovers")
            takeovers_resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise click.ClickException(f"status: could not reach the runner at {where} ({exc})") from exc

    view = runner_resp.json()
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
        click.echo(f"  chunk {esc['chunk_id']}  node={esc['node_id']}  since {esc['closed_at']}{_session_label(esc)}")
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
    resume <runner_id>`` clears that one too."""
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
    client, where = _local_api_client(directory, runner_url)
    try:
        with client:
            resp = client.post(f"/api/chunks/{chunk_id}/takeovers", json={"force": force})
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
                end_resp = client.patch(f"/api/chunks/{chunk_id}/takeovers/{view['takeover_id']}")
                end_resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise click.ClickException(f"takeover: could not reach the runner at {where} ({exc})") from exc
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
    client, where = _local_api_client(directory, runner_url)
    try:
        with client:
            resp = client.post(f"/api/chunks/{chunk_id}/requeues")
            if resp.status_code == 409:
                raise click.ClickException(f"requeue: {resp.json().get('detail', 'chunk is not requeueable')}")
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise click.ClickException(f"requeue: could not reach the runner at {where} ({exc})") from exc
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
    client, where = _local_api_client(directory, runner_url)
    try:
        with client:
            resp = client.post("/api/selftests", json={"harness": coding_harness})
            if resp.status_code == 422:
                raise click.ClickException(resp.json().get("detail", "unknown coding harness"))
            resp.raise_for_status()
            run = resp.json()
            deadline = time.monotonic() + _SELFTEST_POLL_TIMEOUT
            while run["status"] == "running":
                if time.monotonic() > deadline:
                    raise click.ClickException(
                        f"selftest {run['id']} did not finish within {_SELFTEST_POLL_TIMEOUT:g}s "
                        "— the runner may be wedged"
                    )
                time.sleep(_SELFTEST_POLL_INTERVAL)
                resp = client.get(f"/api/selftests/{run['id']}")
                resp.raise_for_status()
                run = resp.json()
    except httpx.HTTPError as exc:
        raise click.ClickException(f"selftest: could not reach the runner at {where} ({exc})") from exc

    for check in run["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        click.echo(f"[{mark}] {check['name']}: {check['detail']}")
    if run["status"] != "passed":
        if run.get("error"):
            click.echo(f"selftest error: {run['error']}", err=True)
        click.echo(f"selftest {run['id']} FAILED for {coding_harness}", err=True)
        raise click.exceptions.Exit(1)
    click.echo(f"selftest {run['id']} passed for {coding_harness}")

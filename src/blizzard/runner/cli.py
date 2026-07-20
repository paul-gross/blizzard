"""``blizzard runner <cmd>`` — the machine-local surface.

Client verbs are pure clients of the runner's local API; ``host`` *becomes* the
runner daemon. Only ``init`` / ``migrate`` / ``host`` are implemented in
the scaffold — the rest are stubs that name themselves, present in ``--help`` and
filled in by the backend builder. Worker-hook verbs (``heartbeat``, ``session-end``,
``ask``, ``pm-items``) take their identity from the spawn-injected environment and pass
no identity arguments.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
import types
from pathlib import Path

import click
import httpx
import uvicorn

from blizzard.cli.host_directory import resolve_host_directory
from blizzard.cli.param_rank import source_rank
from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.runner.app import build_hosted_app
from blizzard.runner.config import ConfigError, RunnerConfig, socket_path_for
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

# The runtime root the dir-taking verbs resolve, highest to lowest: an explicit
# ``--dir`` (or ``init``'s DIRECTORY), then ``BZ_RUNNER_DIR``, then the cwd. The env rung
# is what lets winter's per-env band (`[env.<name>.vars]`) aim one feature env at a
# chosen runtime root — a store snapshot, or a shared dir during an exclusive handoff —
# without a bespoke command line per invocation (issue #39). Selectable, not shareable:
# the store is still single-writer, so two live daemons on one `runner.db` remains unsafe.
ENV_RUNNER_DIR = "BZ_RUNNER_DIR"
DEFAULT_DIR = "."

# Spawn-injected worker identity the heartbeat hook inherits.
# `BLIZZARD_*` is the worker namespace — per-process-tree execution truth the runner mints
# at spawn — and is distinct from the operator's `BZ_*` config namespace below.
ENV_LEASE_ID = "BLIZZARD_LEASE_ID"
ENV_RUNNER_URL = "BLIZZARD_RUNNER_URL"
# The lease's minted capability token (issue #113, Phase 1) — authorizes `attach`
# against this worker's own lease, nothing else.
ENV_LEASE_TOKEN = "BLIZZARD_LEASE_TOKEN"
# The operator's TCP door onto the local API (issue #43) — the `BZ_*` namespace, and the
# override for when the socket is not the right address (a remote-ish Tailnet reach, or a
# daemon whose runtime dir this shell cannot see).
ENV_LOCAL_API_URL = "BZ_RUNNER_URL"
_HEARTBEAT_TIMEOUT = 5.0
# A PM-item read fans out runner -> hub -> vendor, so it is given a longer budget
# than the millisecond-cheap hook posts.
_PM_ITEMS_TIMEOUT = 20.0
# The operator's declarative pause/start verbs are pure clients of the runner's own local
# API (issue #43) — a machine-local round trip, so they get a hook-scale budget rather
# than the hub-client one.
_LOCAL_CLIENT_TIMEOUT = 5.0
# `selftest` (issue #54) polls its job resource at this cadence — each poll is a
# machine-local read of already-computed state, so a short interval costs nothing.
_SELFTEST_POLL_INTERVAL = 0.2
# A CLI-side backstop above the server's own run budget (`SelfTestService`'s
# `run_budget_seconds`): the server-side budget is authoritative and always resolves
# the run, but a bounded poll here means the CLI itself never spins forever even
# against a runner that can't reach that code (an old daemon, a stuck event loop).
_SELFTEST_POLL_TIMEOUT = 600.0


# The local verbs address the runner's own API through one of its two doors: the
# socket under `--dir` (the default — no port, found from the runtime dir alone) or the TCP
# listener named by `--runner-url`. Ranked by where each value came from (`param_rank.py`),
# because `--dir` always *has* a value: it defaults to "." and takes $BZ_RUNNER_DIR, which
# winter's per-env band exports ambiently across a whole feature env — so "is it set?"
# cannot mean "did the operator choose it?". An explicit flag beats an ambient variable;
# only a genuine tie on the command line is ambiguous. Both from the environment resolves
# to the socket, the default transport.
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
    # `PeriodicDriver` resolves `workspace_prompt`/`runner_prompt` in its constructor
    # (on this thread) rather than inside its background loop thread — a configured-but-
    # missing prompt file raises `ConfigError` here, before any socket binds, instead of
    # silently killing the loop thread later while uvicorn keeps serving `/api/health` 200s.
    try:
        driver = PeriodicDriver(config, interval_seconds=interval)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    # Two doors onto the one app (issue #43): the unix socket the CLI's local verbs
    # address, and the TCP port the browser and the worker hooks address. Bound up front so
    # a clash fails startup loudly; served by a single `Server` below, which is what keeps
    # the shutdown path (and its resume-intent marking) exactly as it was.
    try:
        sockets = bind_listeners(config)
    except ListenerError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"serving blizzard-runner on {config.host}:{config.port} and {config.socket_path} (loop tick {interval}s)"
    )

    # The graceful-restart resume marker lives in this frame's `finally`, so it must run
    # *after* `server.run()` returns — which means SIGTERM must drain the server, not hard-exit
    # the process. Both handlers that can be in force do exactly that by setting `should_exit`:
    #   * ours (`_drain`) below, and
    #   * uvicorn's own `handle_exit`, which its `run()` installs around serving.
    # So whichever is active, `run()` returns and the marking is reached. We register ours first,
    # then suppress uvicorn's installer *only on versions that expose it* (older uvicorn's
    # `install_signal_handlers`) so ours stays in force; on versions that renamed it to the
    # `capture_signals` context manager (uvicorn ≥ 0.29) there is nothing to suppress and we lean
    # on uvicorn's own graceful `handle_exit` — equivalent for our purpose. Guarding the monkey-
    # patch with `hasattr` keeps a uvicorn upgrade from crashing startup on a missing attribute.
    # A `kill -9` skips all of this — the ungraceful-crash boundary.
    # Host/port here are for uvicorn's own startup log only: `run(sockets=...)` below serves
    # exactly the pre-bound sockets and never consults them (uvicorn Server.startup).
    server = uvicorn.Server(uvicorn.Config(app, host=config.host, port=config.port))

    def _drain(_signum: int, _frame: types.FrameType | None) -> None:
        server.should_exit = True

    signal.signal(signal.SIGTERM, _drain)
    signal.signal(signal.SIGINT, _drain)
    if hasattr(server, "install_signal_handlers"):
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

    # Ungraceful-restart recovery (#13): a `kill -9` / OOM / reboot never ran the
    # graceful shutdown marker below, so before the loop starts we detect the sessions killed
    # mid-work — dead pid, no recorded session-end, heartbeat not stale — and mark them for the
    # same startup RESUME the first tick runs. The mark is the only ungraceful-specific step;
    # everything downstream is the graceful path's machinery (kill-first, unchanged epoch, the
    # abandon-if-reassigned ownership fence). A clean `blizzard runner init` has no leases, so this is a no-op.
    resumable = mark_crash_resume_intents_on_startup(config)
    if resumable:
        click.echo(f"marked {resumable} crash-interrupted lease(s) for restart-resume")

    driver.start()  # startup recovery is REAP running first inside the tick
    try:
        server.run(sockets=sockets)
    finally:
        # Stop the loop first so no in-flight tick races the marking: `stop()` blocks on the
        # tick thread (an unbounded join — see PeriodicDriver.stop) so the loop is quiescent
        # before we mark every in-flight lease for the next startup's RESUME.
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

    The steppable-loop driver for tests and the e2e (``bzh:steppable-loop``): a
    single pass against the live hub and workspace, then exit. Refuses on a store
    revision mismatch, like ``host``.
    """
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


@runner.command()
def heartbeat() -> None:
    """Worker hook: record a lease heartbeat (identity from the environment).

    A pure client of the runner's local API: the ``PostToolUse`` hook runs
    this on every tool call, and it posts to ``BLIZZARD_RUNNER_URL`` for the lease in
    ``BLIZZARD_LEASE_ID`` — both inherited from the spawn environment, so no arguments.
    It fails **soft**: a hook must never break the
    worker's tool call, so a missing identity or an unreachable runner is reported to
    stderr and the command still exits 0.
    """
    lease_id = os.environ.get(ENV_LEASE_ID)
    runner_url = os.environ.get(ENV_RUNNER_URL)
    if not lease_id or not runner_url:
        click.echo(f"heartbeat: no {ENV_LEASE_ID}/{ENV_RUNNER_URL} in the environment; skipping", err=True)
        return
    try:
        resp = httpx.post(
            f"{runner_url.rstrip('/')}/api/heartbeat",
            json={"lease_id": lease_id},
            timeout=_HEARTBEAT_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:  # soft-fail — never break the worker's tool call
        click.echo(f"heartbeat: could not reach the runner ({exc}); skipping", err=True)


@runner.command("session-end")
def session_end() -> None:
    """Worker hook: record the session's exit (identity from the environment).

    A pure client of the runner's local API: the ``SessionEnd`` hook runs this
    when the worker's Claude session exits, and it posts to ``BLIZZARD_RUNNER_URL`` for
    the lease in ``BLIZZARD_LEASE_ID`` — both inherited from the spawn environment, so no
    arguments. The fact is the "declared done" signal
    (exit-is-done) startup crash-recovery reads to tell a clean exit from a worker
    killed mid-work. It fails **soft**, like the heartbeat: a hook must never break
    the worker's exit, so a missing identity or an unreachable runner is reported to stderr
    and the command still exits 0.
    """
    lease_id = os.environ.get(ENV_LEASE_ID)
    runner_url = os.environ.get(ENV_RUNNER_URL)
    if not lease_id or not runner_url:
        click.echo(f"session-end: no {ENV_LEASE_ID}/{ENV_RUNNER_URL} in the environment; skipping", err=True)
        return
    try:
        resp = httpx.post(
            f"{runner_url.rstrip('/')}/api/leases/{lease_id}/session-end",
            timeout=_HEARTBEAT_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:  # soft-fail — never break the worker's exit
        click.echo(f"session-end: could not reach the runner ({exc}); skipping", err=True)


@runner.command()
@click.argument("prompt")
@click.option("--options", default=None, help="Pipe-separated answer options.")
def ask(prompt: str, options: str | None) -> None:
    """Worker: ask-and-exit; the ask fact is durable before the worker exits.

    A pure client of the runner's local API: the worker runs this on an
    undecidable choice, and it posts the question for the lease in ``BLIZZARD_LEASE_ID``
    to ``BLIZZARD_RUNNER_URL`` — both inherited from the spawn environment, so no
    identity arguments. The ask is a
    durable runner-store fact before this returns and the worker ends its turn.
    """
    lease_id = os.environ.get(ENV_LEASE_ID)
    runner_url = os.environ.get(ENV_RUNNER_URL)
    if not lease_id or not runner_url:
        raise click.ClickException(f"ask: no {ENV_LEASE_ID}/{ENV_RUNNER_URL} in the environment")
    body: dict[str, object] = {"question": prompt}
    if options:
        body["options"] = [o for o in options.split("|") if o]
    try:
        resp = httpx.post(
            f"{runner_url.rstrip('/')}/api/leases/{lease_id}/asks",
            json=body,
            timeout=_HEARTBEAT_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise click.ClickException(f"ask: could not record the question ({exc})") from exc
    click.echo(resp.json().get("question_id", ""))


@runner.command()
@click.option("--name", required=True, help="The `produces:` name this content is submitted for.")
def attach(name: str) -> None:
    """Worker: durably submit an explicit artifact for a ``produces:`` name (stdin).

    A pure client of the runner's local API: the worker pipes the artifact's content
    on stdin, and this posts it for the lease in ``BLIZZARD_LEASE_ID`` to
    ``BLIZZARD_RUNNER_URL``, authorized by the lease token in ``BLIZZARD_LEASE_TOKEN`` —
    all three inherited from the spawn environment, so no identity arguments. A
    rejection (a wrong/missing token, an unknown lease, an unreachable runner) exits
    non-zero so the worker learns it rather than silently losing the submission.
    """
    lease_id = os.environ.get(ENV_LEASE_ID)
    runner_url = os.environ.get(ENV_RUNNER_URL)
    lease_token = os.environ.get(ENV_LEASE_TOKEN)
    if not lease_id or not runner_url:
        raise click.ClickException(f"attach: no {ENV_LEASE_ID}/{ENV_RUNNER_URL} in the environment")
    content = click.get_text_stream("stdin").read()
    try:
        resp = httpx.post(
            f"{runner_url.rstrip('/')}/api/leases/{lease_id}/attachments",
            json={"name": name, "content": content},
            headers={"X-Blizzard-Lease-Token": lease_token} if lease_token else {},
            timeout=_HEARTBEAT_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise click.ClickException(f"attach: could not record {name!r} ({exc})") from exc


@runner.command("pm-items")
@click.argument("chunk_id")
def pm_items(chunk_id: str) -> None:
    """Worker: pass-through read of a chunk's PM items (runner -> hub -> vendor).

    A pure client of the runner's local API: the build node reads its
    chunk's issue body + comment thread through the runner's proxy route
    (``graphs/prompts/build.md``), which forwards to the hub — the worker never talks
    to the hub or the PM system directly. The runner URL is inherited from the spawn
    environment (``BLIZZARD_RUNNER_URL``), so no identity argument; the items print as
    JSON (``{items: [{source, ref, label, web_url, fetched_at, body, comments, error}, ...]}``)
    — one entry per pointer — for the worker to consume.
    """
    runner_url = os.environ.get(ENV_RUNNER_URL)
    if not runner_url:
        raise click.ClickException(f"pm-items: no {ENV_RUNNER_URL} in the environment")
    try:
        resp = httpx.get(
            f"{runner_url.rstrip('/')}/api/chunks/{chunk_id}/pm-items",
            timeout=_PM_ITEMS_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise click.ClickException(f"pm-items: could not read the PM item ({exc})") from exc
    click.echo(resp.text)


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
    """The machine-local view: capacities, held environments, open asks, escalations,
    open takeovers (issue #51).

    A pure client of the runner's local API — socket or ``--runner-url``, the same
    door ``pause``/``start`` use — so every section here is this runner's own local
    read and the view renders fully with the hub unreachable; hub reachability itself
    is reported, not assumed. ``GET /runner`` + ``GET /leases`` + ``GET /environments``
    + ``GET /asks?open=true`` + ``GET /escalations`` + ``GET /takeovers`` — no store
    access, no hub call. The open-takeovers section is the recovery surface for a
    takeover a stranded CLI (an interrupted terminal that never reached the end-PATCH,
    issue #52) left open with no other way to find its ``takeover_id``.
    """
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

    envs = envs_resp.json().get("items", [])
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
        click.echo(f"  chunk {esc['chunk_id']}  node={esc['node_id']}  since {esc['closed_at']}")
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
    """Declarative control: pause this runner — it starts no new workers (issue #45).

    This runner's **own** brake — "it says it won't try" — and a pure client of its local
    API (``PATCH /runner``), so it works with the hub unreachable. It blocks every spawn
    site (FILL, restart-resume, an answer-resume, ADVANCE's next-node, a requeue or
    claim-adopt respawn, and the judgement resume that would elicit a verdict from an
    exited worker's session) and defers both REAP's kill of a stalled worker and
    escalation to a human at an exhausted retry budget, wherever it would happen. No
    retry is consumed anywhere; a live worker already running is left alone (this is not
    a drain, and it does not kill). A worker that *exits* while paused simply waits
    unjudged — judging it is itself a spawn — until the brake clears. It is distinct from
    the hub's brake (``blizzard hub pause <runner_id>``), which coerces a runner from the
    fleet side and keeps its claims-only meaning, and each is cleared where it was set.
    Clear this one with ``blizzard runner start``."""
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
    """Take over a parked chunk: exec the interactive resume command in this terminal (issue #52).

    A pure client of the runner's local API — the same door ``status``/``pause`` use.
    ``POST /chunks/{id}/takeovers`` records the takeover fact before anything else runs
    (so no loop step can respawn or judge the session while it is open) and returns the
    adapter-composed interactive command; this verb then execs that command as its own
    child, inheriting this terminal — the daemon itself never touches a TTY. ``--force``
    supersedes a live worker attempt instead of refusing (``409``): the runner kills it
    itself, after the fact lands, consuming no retry and recording no escalation. Once
    the child exits — or this terminal is interrupted (``Ctrl-C``) — ``PATCH
    .../takeovers/{tid}`` marks the takeover ended, whatever the child's exit status: the
    end-PATCH runs in a ``finally`` around the child so a stranded open takeover (which
    REAP/ADVANCE would otherwise skip forever) cannot outlive an interrupted session."""
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
                exit_code = subprocess.call(view["command"], shell=True, cwd=view["workdir"])
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

    A pure client of the runner's local API — the same door ``status``/``pause``/``takeover``
    use. ``POST /chunks/{id}/requeues`` appends the fact that clears the chunk's local
    needs_human hold (escalated, or held by an ended takeover — the pasted-command flow
    works too, with no recorded takeover at all); the next FILL spawns a fresh attempt —
    new session, new lease, fresh epoch — at the chunk's current node. The chunk's route
    is never released and it never re-enters the hub's queue; it resumes exactly where it
    stood. Refused with ``409`` while the chunk's takeover is still open (end the
    interactive session first — one process per session) or while the chunk is not
    parked needs_human."""
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
    """Adapter-drift canary before an unattended period (issue #54).

    Exercises CODING_HARNESS against a throwaway scratch repo — spawn with a
    pre-assigned session id and exit-is-done detection, a trivial end-to-end
    edit+commit, verdict elicitation, an automated follow-up resume, and
    resume-command composition — touching no chunk, lease, environment, or hub. A
    pure client of the runner's local API (``POST``/``GET /selftests``): it posts the
    run, polls it to completion, prints each check's pass/fail, and exits non-zero on
    any failure.
    """
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

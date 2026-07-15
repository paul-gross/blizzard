"""``blizzard runner <cmd>`` — the machine-local surface (design/cli.md).

Client verbs are pure clients of the runner's local API; ``host`` *becomes* the
runner daemon (D-061). Only ``init`` / ``migrate`` / ``host`` are implemented in
the scaffold — the rest are stubs that name themselves, present in ``--help`` and
filled in by the backend builder. Worker-hook verbs (``heartbeat``, ``session-end``,
``ask``, ``pm-items``) take their identity from the spawn-injected environment and pass
no identity arguments.
"""

from __future__ import annotations

import os
import signal
import types
from pathlib import Path

import click
import httpx
import uvicorn

from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.runner.app import build_hosted_app
from blizzard.runner.config import ConfigError, RunnerConfig
from blizzard.runner.loop.build import (
    PeriodicDriver,
    mark_crash_resume_intents_on_startup,
    mark_resume_intents_on_shutdown,
    run_single_tick,
)
from blizzard.runner.runtime import ensure_current_revision, init_environment, migrate, migration_runner

ENV_TICK_SECONDS = "BZ_RUNNER_TICK_SECONDS"
DEFAULT_TICK_SECONDS = 30.0

# Spawn-injected worker identity the heartbeat hook inherits (design/harness-adapters.md).
ENV_LEASE_ID = "BLIZZARD_LEASE_ID"
ENV_RUNNER_URL = "BLIZZARD_RUNNER_URL"
_HEARTBEAT_TIMEOUT = 5.0
# A PM-item read fans out runner -> hub -> vendor, so it is given a longer budget
# than the millisecond-cheap hook posts (design/runner/api.md).
_PM_ITEMS_TIMEOUT = 20.0
# The operator's declarative pause/start verbs reach the hub directly (D-043), not the
# local API, so they get the hub-client budget rather than a hook timeout.
_HUB_CLIENT_TIMEOUT = 15.0


def _stub(verb: str) -> None:
    raise click.ClickException(f"`blizzard runner {verb}` is not yet implemented (scaffold stub).")


@click.group(invoke_without_command=True)
@click.pass_context
def runner(ctx: click.Context) -> None:
    """Talk to — or become — the blizzard runner."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(host)


@runner.command()
@click.argument("directory", default=".")
def init(directory: str) -> None:
    """Scaffold config + data dir + a migrated store under DIRECTORY. Idempotent."""
    config = init_environment(Path(directory))
    revision = migration_runner(config).current_revision()
    click.echo(f"runner runtime ready at {config.root} (store revision {revision})")


@runner.command("migrate")
@click.option("--dir", "directory", default=".", help="Runner runtime directory.")
@click.option("--down", default=None, help="Reverse migrations down to this revision (e.g. base).")
def migrate_cmd(directory: str, down: str | None) -> None:
    """Apply pending store migrations, or reverse with --down <rev>."""
    try:
        migrate(Path(directory), down=down)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("migrated" if down is None else f"reversed to {down}")


@runner.command()
@click.option("--dir", "directory", default=".", help="Runner runtime directory.")
@click.option("--host", "host_", default=None, help="Bind host (overrides config).")
@click.option("--port", type=int, default=None, help="Bind port (overrides config).")
def host(directory: str, host_: str | None, port: int | None) -> None:
    """Become the blizzard-runner daemon: the reconciliation loop + the local API."""
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
    driver = PeriodicDriver(config, interval_seconds=interval)
    click.echo(f"serving blizzard-runner on {config.host}:{config.port} (loop tick {interval}s)")

    # The graceful-restart resume marker (D-082) lives in this frame's `finally`, so it must run
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
    # A `kill -9` skips all of this — the ungraceful-crash boundary (design/runner/loop.md).
    server = uvicorn.Server(uvicorn.Config(app, host=config.host, port=config.port))

    def _drain(_signum: int, _frame: types.FrameType | None) -> None:
        server.should_exit = True

    signal.signal(signal.SIGTERM, _drain)
    signal.signal(signal.SIGINT, _drain)
    if hasattr(server, "install_signal_handlers"):
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

    # Ungraceful-restart recovery (#13, D-082): a `kill -9` / OOM / reboot never ran the
    # graceful shutdown marker below, so before the loop starts we detect the sessions killed
    # mid-work — dead pid, no recorded session-end, heartbeat not stale — and mark them for the
    # same startup RESUME the first tick runs. The mark is the only ungraceful-specific step;
    # everything downstream is the graceful path's machinery (kill-first, unchanged epoch, the
    # D-088 ownership fence). A clean `blizzard runner init` has no leases, so this is a no-op.
    resumable = mark_crash_resume_intents_on_startup(config)
    if resumable:
        click.echo(f"marked {resumable} crash-interrupted lease(s) for restart-resume")

    driver.start()  # startup recovery is REAP running first inside the tick
    try:
        server.run()
    finally:
        # Stop the loop first so no in-flight tick races the marking: `stop()` blocks on the
        # tick thread (an unbounded join — see PeriodicDriver.stop) so the loop is quiescent
        # before we mark every in-flight lease for the next startup's RESUME (D-082).
        driver.stop()
        marked = mark_resume_intents_on_shutdown(config)
        if marked:
            click.echo(f"marked {marked} in-flight lease(s) for restart-resume")


@runner.command("tick")
@click.option("--dir", "directory", default=".", help="Runner runtime directory.")
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

    A pure client of the runner's local API (D-023): the ``PostToolUse`` hook runs
    this on every tool call, and it posts to ``BLIZZARD_RUNNER_URL`` for the lease in
    ``BLIZZARD_LEASE_ID`` — both inherited from the spawn environment, so no arguments
    (design/harness-adapters.md). It fails **soft**: a hook must never break the
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

    A pure client of the runner's local API (D-023): the ``SessionEnd`` hook runs this
    when the worker's Claude session exits, and it posts to ``BLIZZARD_RUNNER_URL`` for
    the lease in ``BLIZZARD_LEASE_ID`` — both inherited from the spawn environment, so no
    arguments (design/harness-adapters.md). The fact is the "declared done" signal
    (exit-is-done, D-055) startup crash-recovery reads to tell a clean exit from a worker
    killed mid-work (D-082). It fails **soft**, like the heartbeat: a hook must never break
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

    A pure client of the runner's local API (D-023): the worker runs this on an
    undecidable choice, and it posts the question for the lease in ``BLIZZARD_LEASE_ID``
    to ``BLIZZARD_RUNNER_URL`` — both inherited from the spawn environment, so no
    identity arguments (design/harness-adapters.md, [ask-answer.md]). The ask is a
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


@runner.command("pm-items")
@click.argument("chunk_id")
def pm_items(chunk_id: str) -> None:
    """Worker: pass-through read of a chunk's PM items (runner -> hub -> vendor).

    A pure client of the runner's local API (D-023/D-084): the build node reads its
    chunk's issue body + comment thread through the runner's proxy route
    (``graphs/prompts/build.md``), which forwards to the hub — the worker never talks
    to the hub or the PM system directly. The runner URL is inherited from the spawn
    environment (``BLIZZARD_RUNNER_URL``), so no identity argument; the items print as
    JSON (``{items: [{provider, url, label, fetched_at, body, comments, error}, ...]}``)
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
def status() -> None:
    """The machine-local view: capacities, held environments, open asks, escalations."""
    _stub("status")


@runner.command()
@click.option("--dir", "directory", default=".", help="Runner runtime directory.")
@click.option("--by", "by", default="operator", help="Who is pausing (recorded on the fact).")
def pause(directory: str, by: str) -> None:
    """Declarative control: pause the runner — it stops claiming new work; in-flight chunks run on (D-043).

    The machine-local half of the hub's pause brake: it reads this runner's id and hub URL
    from the runtime config in DIR and POSTs ``/api/runners/{id}/pause`` to the hub. The
    runner reads ``paused`` back on its next pull and adheres. Resume with ``blizzard hub
    resume <runner_id>``."""
    try:
        config = RunnerConfig.load(Path(directory))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    url = f"{config.hub_url.rstrip('/')}/api/runners/{config.runner_id}/pause"
    try:
        resp = httpx.post(url, json={"by": by}, timeout=_HUB_CLIENT_TIMEOUT)
    except httpx.HTTPError as exc:
        raise click.ClickException(f"pause: could not reach the hub at {config.hub_url} ({exc})") from exc
    if resp.status_code == httpx.codes.NOT_FOUND:
        raise click.ClickException(f"runner {config.runner_id} is not registered with the hub at {config.hub_url}")
    try:
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise click.ClickException(f"pause: {exc}") from exc
    click.echo(f"runner {config.runner_id} is now paused — it will stop claiming new work")


@runner.command()
def start() -> None:
    """Declarative control: resume the runner."""
    _stub("start")


@runner.command()
@click.argument("chunk_id")
def takeover(chunk_id: str) -> None:
    """Take over a parked chunk, returning the interactive resume command."""
    _stub("takeover")


@runner.command()
@click.argument("chunk_id")
def requeue(chunk_id: str) -> None:
    """Hand a taken-over chunk back to the fleet."""
    _stub("requeue")


@runner.command()
@click.argument("coding_harness")
def selftest(coding_harness: str) -> None:
    """Adapter-drift canary before an unattended period."""
    _stub("selftest")

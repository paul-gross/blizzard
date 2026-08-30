"""``blizzard runner init``/``migrate``/``host``/``tick`` — scaffold, migrate, and drive the runtime."""

from __future__ import annotations

import os
import signal
import types
from collections.abc import Callable
from pathlib import Path

import click

from blizzard.cli.host_directory import HostDirectory
from blizzard.cli.runtime import build_early_shutdown_server, click_exception_on, run_init, run_migrate
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.config import ConfigError, RunnerConfig
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.listeners import ListenerError, Listeners, Uds
from blizzard.runner.loop.build import LoopWiring, PeriodicDriver, ResumeMarking
from blizzard.runner.loop.process import LinuxProcessProbe
from blizzard.runner.runtime import ensure_current_revision, init_environment, migrate, migration_runner
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.repository import RunnerStoreErrorFactory

ENV_TICK_SECONDS = "BZ_RUNNER_TICK_SECONDS"
DEFAULT_TICK_SECONDS = 30.0


@click.command()
@click.argument("directory", default=DEFAULT_DIR, envvar=ENV_RUNNER_DIR)
def init(directory: str) -> None:
    """Scaffold config + data dir + a migrated store under DIRECTORY. Idempotent.

    DIRECTORY defaults to $BZ_RUNNER_DIR, then the cwd."""
    run_init(
        Path(directory),
        "runner",
        init_environment=init_environment,
        migration_runner=migration_runner,
        config_error=ConfigError,
    )


@click.command("migrate")
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
    run_migrate(Path(directory), down, migrate=migrate, config_error=ConfigError)


@click.command()
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
    with click_exception_on(ConfigError):
        config = RunnerConfig.load(Path(directory), host=host_, port=port)
    with click_exception_on(RevisionMismatchError):
        ensure_current_revision(config)
    # One broker for the process (D2): `host` is the one composer building both the
    # served app and the ticked loop, so every writer and the stream route share it.
    broker = EventBroker()
    app = build_hosted_app(config, events=broker)
    interval = float(os.environ.get(ENV_TICK_SECONDS, DEFAULT_TICK_SECONDS))
    # `PeriodicDriver` resolves its prompt files on this thread, not in the loop thread: a
    # configured-but-missing prompt raises here, before any socket binds.
    with click_exception_on(ConfigError):
        driver = PeriodicDriver(config, interval_seconds=interval, broker=broker)

    # Two doors onto the one app (issue #43), bound up front so a clash fails startup loudly and
    # served by the single `Server` below, which keeps the shutdown path on one frame.
    with click_exception_on(ListenerError):
        sockets = Listeners.of(config).bound()
    click.echo(
        f"serving blizzard-runner on {config.host}:{config.port} and {config.socket_path} (loop tick {interval}s)"
    )

    # The shared early-shutdown wrapper (D1/D3): sets `app.state.shutdown` ahead of
    # uvicorn's own drain, so `server.run()` returns and the `finally` below still runs.
    server = build_early_shutdown_server(app, host=config.host, port=config.port, shutdown_signal=app.state.shutdown)

    # Installed before `server.run()`'s own `capture_signals()` window opens, so a signal in
    # that gap still primes shutdown (D3) rather than being discarded; re-invoking it later is idempotent.
    def _handle_signal(signum: int, frame: types.FrameType | None) -> None:
        server.handle_exit(signum, frame)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Ungraceful-restart recovery (#13): a `kill -9` never ran the graceful shutdown marker below, so
    # sessions killed mid-work are marked here for the same startup RESUME the first tick runs.
    resumable = _resume_marked(config, lambda marking: marking.on_startup())
    if resumable:
        click.echo(f"marked {resumable} crash-interrupted lease(s) for restart-resume")

    driver.start()  # startup recovery is REAP running first inside the tick
    try:
        server.run(sockets=sockets)
    finally:
        # Stop the loop first so no in-flight tick races the marking: `stop()` blocks on the tick
        # thread, so the loop is quiescent before every in-flight lease is marked.
        driver.stop()
        marked = _resume_marked(config, lambda marking: marking.on_shutdown())
        if marked:
            click.echo(f"marked {marked} in-flight lease(s) for restart-resume")
        # uvicorn closes a pre-bound socket but does not unlink its file; leaving it would
        # make the next start take the stale-corpse path in `Uds.bound` for nothing.
        Uds(config.socket_path).unlink()


def _resume_marked(config: RunnerConfig, mark: Callable[[ResumeMarking], int]) -> int:
    """Wire one :class:`ResumeMarking` over its own short-lived engine — the engine lifecycle
    ``ResumeMarking`` itself used to own, relocated here now that its store is injected."""
    engine = create_engine_from_url(config.db_url)
    try:
        store = SqlAlchemyRunnerStore(engine, RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))
        return mark(ResumeMarking(store, SystemClock(), LinuxProcessProbe()))
    finally:
        engine.dispose()


@click.command("tick")
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
    with click_exception_on(ConfigError):
        config = RunnerConfig.load(Path(directory))
    with click_exception_on(RevisionMismatchError):
        ensure_current_revision(config)
    LoopWiring.of(config).tick_once()
    click.echo("tick complete")

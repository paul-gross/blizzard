"""``blizzard runner init``/``migrate``/``host``/``tick`` — scaffold, migrate, and drive the runtime."""

from __future__ import annotations

import os
import signal
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import click

from blizzard.cli.host_directory import HostDirectory
from blizzard.cli.runtime import build_early_shutdown_server, click_exception_on, run_init, run_migrate
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.runner.app import HostedApp, build_hosted_app
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.composition import RunnerProcess, build_read_stores, build_runner_process
from blizzard.runner.config import ConfigError, RunnerConfig
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.bundle_layouts import publish_harness_bundle
from blizzard.runner.listeners import ListenerError, Listeners, Uds
from blizzard.runner.loop.build import LoopWiring, PeriodicDriver
from blizzard.runner.runtime import ensure_current_revision, init_environment, migrate, migration_runner
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.stores import RunnerReadStores

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
    _publish_harness_bundle(config)
    for missing in config.missing_worker_path_prepend_entries:
        click.echo(f"warning: [worker] path_prepend entry does not exist: {missing}")
    with click_exception_on(RevisionMismatchError):
        ensure_current_revision(config)
    # One broker for the process: `host` is the one composer building both the
    # served app and the ticked loop, so every writer and the stream route share it.
    broker = EventBroker()
    graph = build_runner_process(config, events=broker)
    try:
        hosted = build_hosted_app(config, process_graph=graph)
        try:
            _serve_host(config, graph, hosted)
        finally:
            hosted.close()
    finally:
        graph.close()


def _publish_harness_bundle(config: RunnerConfig) -> None:
    """Load the operator's harness-config bundle and publish its snapshot before any work is
    accepted; a runtime with no `[harness] config_dir` is left untouched."""
    if config.harness_config_dir is None:
        return
    with click_exception_on(ConfigError):
        snapshot = publish_harness_bundle(config.harness_config_dir, config.root)
    click.echo(snapshot.summary())


def _serve_host(config: RunnerConfig, graph: RunnerProcess, hosted: HostedApp) -> None:
    app = hosted.app
    interval = float(os.environ.get(ENV_TICK_SECONDS, DEFAULT_TICK_SECONDS))
    # `PeriodicDriver` resolves its prompt files on this thread, not in the loop thread: a
    # configured-but-missing prompt raises here, before any socket binds.
    with click_exception_on(ConfigError):
        driver = PeriodicDriver(config, interval_seconds=interval, process_graph=graph)

    # Two doors onto the one app, bound up front so a clash fails startup loudly and
    # served by the single `Server` below, which keeps the shutdown path on one frame.
    with click_exception_on(ListenerError):
        sockets = Listeners.of(config).bound()
    click.echo(
        f"serving blizzard-runner on {config.host}:{config.port} and {config.socket_path} (loop tick {interval}s)"
    )

    started = False
    try:
        # The shared early-shutdown wrapper sets the stream's shutdown signal
        # ahead of uvicorn's drain. Bound sockets belong to this whole startup.
        server = build_early_shutdown_server(
            app, host=config.host, port=config.port, shutdown_signal=app.state.shutdown
        )

        # Installed before `server.run()`'s own `capture_signals()` window opens.
        def _handle_signal(signum: int, frame: types.FrameType | None) -> None:
            server.handle_exit(signum, frame)

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        # Mark crash-orphaned sessions before the first reconciliation tick.
        resumable = hosted.resume.on_startup()
        if resumable:
            click.echo(f"marked {resumable} crash-interrupted lease(s) for restart-resume")

        driver.start()  # startup recovery is REAP running first inside the tick
        started = True
        server.run(sockets=sockets)
    finally:
        try:
            if started:
                # Quiesce before marking; the spawner executor and engine outlive
                # the drain and are closed by the outer host frame.
                driver.stop()
                marked = hosted.resume.on_shutdown()
                if marked:
                    click.echo(f"marked {marked} in-flight lease(s) for restart-resume")
        finally:
            # uvicorn closes a pre-bound socket but does not unlink its file.
            Uds(config.socket_path).unlink()


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
    _publish_harness_bundle(config)
    with click_exception_on(RevisionMismatchError):
        ensure_current_revision(config)
    LoopWiring.of(config).tick_once()
    click.echo("tick complete")


@contextmanager
def read_stores(config: RunnerConfig) -> Iterator[RunnerReadStores]:
    """The runner's read-only store bundle for a short-lived CLI verb — builds its own
    engine and disposes it on exit, so a caller never holds
    the engine past the bundle's use."""
    engine = create_engine_from_url(config.db_url)
    try:
        yield build_read_stores(engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))
    finally:
        engine.dispose()

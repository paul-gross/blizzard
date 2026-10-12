"""``blizzard runner init``/``migrate``/``host``/``tick`` — scaffold the runtime and join its hub,
migrate, and drive the runtime."""

from __future__ import annotations

import functools
import os
import signal
import types
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path

import click
import httpx

from blizzard.cli.host_directory import HostDirectory
from blizzard.cli.operator_trace import ITraceHeaders, OperatorTrace
from blizzard.cli.runtime import build_early_shutdown_server, click_exception_on, run_init, run_migrate
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.operator_sessions.internal.session_file import SessionFile
from blizzard.foundation.roles import collaborator, dto
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.runner.app import HostedApp, build_hosted_app
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.cli.store import refuse_while_held
from blizzard.runner.composition import (
    RunnerProcess,
    build_host_read_stores,
    build_read_stores,
    build_runner_platform_tracing,
    build_runner_process,
    build_runner_received_telemetry,
)
from blizzard.runner.config import ConfigError, RunnerConfig
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.bundle import BundleSnapshot
from blizzard.runner.harness.wiring import publish_harness_bundle
from blizzard.runner.hub.bootstrap import (
    BootstrapStop,
    BootstrapStopped,
    HeldToken,
    IRunnerStoreAside,
    RunnerBootstrap,
    TokenNotWritten,
)
from blizzard.runner.hub.client import (
    HubClientError,
    IHubRunnerAdmin,
    ITokenIdentityReader,
    OperatorTenant,
    RunnerAddRefusalReason,
    RunnerAddRefused,
    TokenIdentity,
)
from blizzard.runner.hub.identity import RunnerIdentityHolder
from blizzard.runner.hub.internal.http_hub import HttpHubClient, HttpHubRunnerAdmin
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.listeners import ListenerError, Listeners, Uds
from blizzard.runner.loop_wiring import LoopWiring, PeriodicDriver
from blizzard.runner.runtime import (
    Runtime,
    StoreResetRefused,
    ensure_current_revision,
    init_environment,
    migrate,
    migration_runner,
    reset_command,
)
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.stores import HostReadStores, RunnerReadStores
from blizzard.runner.tracing.sweep import PendingTracingRejection
from blizzard.runner.usage.periodic_pass_driver import PeriodicPassDriver

ENV_TICK_SECONDS = "BZ_RUNNER_TICK_SECONDS"
DEFAULT_TICK_SECONDS = 30.0


#: Bounds each of ``init``'s hub calls, so an unresponsive hub fails the command instead of hanging it.
_INIT_HUB_TIMEOUT_SECONDS = 15.0

type InitHubClients = tuple[ITokenIdentityReader, IHubRunnerAdmin]


@contextmanager
def _http_hub_clients(hub_url: str, runner_token: str, operator_token: str | None) -> Iterator[InitHubClients]:
    """The identity read under the runner's own token, and the add under the operator's session."""
    runner_headers = {"Authorization": f"Bearer {runner_token}"} if runner_token else {}
    with (
        httpx.Client(base_url=hub_url, timeout=_INIT_HUB_TIMEOUT_SECONDS, headers=runner_headers) as runner_http,
        httpx.Client(base_url=hub_url, timeout=_INIT_HUB_TIMEOUT_SECONDS) as operator_http,
    ):
        yield HttpHubClient(runner_http), HttpHubRunnerAdmin(operator_http, operator_token=operator_token)


@collaborator
@dataclass(frozen=True)
class InitCollaborators:
    """What ``init`` reaches the hub through: an invoker hands its own to the root group as ``obj``;
    with none, ``init`` builds the HTTP one."""

    hub_clients: Callable[[str, str, str | None], AbstractContextManager[InitHubClients]]

    def join_with(self, config: RunnerConfig) -> AbstractContextManager[InitHubClients]:
        """The identity read and the add for ``config``'s hub, under its held token and the operator's session."""
        return self.hub_clients(config.hub_url, config.hub_token, SessionFile.of().load(config.hub_url))


@click.command()
@click.argument("directory", default=DEFAULT_DIR, envvar=ENV_RUNNER_DIR)
@click.option("--hub", "hub_url", default=None, help="The hub a fresh config names (overrides $BZ_HUB_URL).")
@click.option(
    "--allow-readd",
    is_flag=True,
    help="Add the runner again when its hub does not know its token — only after that hub's data was reset.",
)
@click.option(
    "--tenant",
    "tenant_id",
    default=None,
    metavar="TENANT_ID",
    help="Add the runner in this tenant, by id; needed only when you may enter several.",
)
@click.option(
    "--project",
    "projects",
    multiple=True,
    metavar="PROJECT",
    help="A project the runner serves, by id or slug; repeat for several. Default: `default` in a fresh config.",
)
@click.pass_context
def init(
    ctx: click.Context,
    directory: str,
    hub_url: str | None,
    allow_readd: bool,
    tenant_id: str | None,
    projects: tuple[str, ...],
) -> None:
    """Scaffold config + data dir + a migrated store under DIRECTORY, then join the runner to its
    hub. Idempotent.

    A runner holding no token is added at the hub — under your `blizzard hub login` session when you
    hold one, in the tenant --tenant names or else the only one you may enter — and its token
    written to DIRECTORY/.env. A held token whose runner serves another tenant than --tenant names stops: a
    runner never moves between tenants. A fresh config serves the projects --project names, else
    `default`; the runner is added serving them, or, with none named, the tenant's only project.
    DIRECTORY defaults to $BZ_RUNNER_DIR, then the cwd."""
    named = projects or None
    run_init(
        Path(directory),
        "runner",
        init_environment=functools.partial(init_environment, hub_url=hub_url, projects=named),
        migration_runner=migration_runner,
        config_error=ConfigError,
    )
    with click_exception_on(ConfigError):
        config = RunnerConfig.load(Path(directory))
    collaborators = ctx.find_object(InitCollaborators) or InitCollaborators(_http_hub_clients)
    with collaborators.join_with(config) as (identity, admin):
        _join_hub(
            config,
            identity,
            admin,
            _RuntimeStore(config, OperatorTrace.source(ctx)),
            _Rerun(config.root, tenant_id=tenant_id, allow_readd=allow_readd, projects=named),
        )


@dto
@dataclass(frozen=True)
class _Rerun:
    """How this init was asked to join — the tenant and projects it names and whether a re-add is
    allowed — and the init command it suggests re-running, which carries the same tenant and projects."""

    root: Path
    tenant_id: str | None
    allow_readd: bool
    projects: tuple[str, ...] | None = None

    def again(self) -> str:
        """This init's own command, its tenant, projects and flags kept."""
        return _init_command(self.root, tenant_id=self.tenant_id, allow_readd=self.allow_readd, projects=self.projects)

    def readding(self) -> str:
        """This init's command with a re-add allowed, its tenant and projects kept."""
        return _init_command(self.root, tenant_id=self.tenant_id, allow_readd=True, projects=self.projects)

    def naming_a_tenant(self) -> str:
        """This init's command with a placeholder for the tenant id the operator picks."""
        return _init_command(self.root, tenant_id="<tenant_id>", allow_readd=self.allow_readd, projects=self.projects)

    def keeping_the_runner(self) -> str:
        """This init's command naming no tenant and allowing no re-add — the one that keeps the held runner."""
        return _init_command(self.root, tenant_id=None, allow_readd=False, projects=self.projects)


def _init_command(
    root: Path, *, tenant_id: str | None, allow_readd: bool, projects: tuple[str, ...] | None = None
) -> str:
    """``blizzard runner init`` for ``root``, naming ``tenant_id`` when given, each of ``projects``,
    and passing ``--allow-readd`` when ``allow_readd``."""
    return " ".join(
        [f"blizzard runner init {root}"]
        + ([f"--tenant {tenant_id}"] if tenant_id else [])
        + [f"--project {project}" for project in projects or ()]
        + (["--allow-readd"] if allow_readd else [])
    )


class _RuntimeStore:
    """The runtime's own store, as ``init`` sees it while the daemon is down."""

    def __init__(self, config: RunnerConfig, trace: ITraceHeaders) -> None:
        self.config = config
        self.trace = trace

    def stored_runner_id(self) -> str | None:
        with read_stores(self.config) as stores:
            stored = stores.identity.runner_identity()
        return stored.runner_id if stored is not None else None

    def set_aside(self) -> None:
        refuse_while_held(self.config.root, self.trace, action="set the old runner's store aside")
        with click_exception_on(ConfigError, StoreResetRefused):
            aside = Runtime(self.config.root).reset_store(now=SystemClock().now())
        for path in aside.moved:
            click.echo(f"set the old runner's store aside as {path}")


def _join_hub(
    config: RunnerConfig,
    identity: ITokenIdentityReader,
    admin: IHubRunnerAdmin,
    store: IRunnerStoreAside,
    rerun: _Rerun,
) -> None:
    """Keep the runner the held token names, or add one and write its token; any stop exits
    non-zero with nothing added."""
    token_file = HubTokenFile.of(config.root, config.token_env)
    held = (
        HeldToken(config.hub_token, from_process_env=bool(os.environ.get(config.token_env)))
        if config.hub_token
        else None
    )
    hub = config.hub_url
    try:
        joined = RunnerBootstrap(identity, admin, token_file, store).join(
            config.name, held=held, allow_readd=rerun.allow_readd, tenant_id=rerun.tenant_id, projects=rerun.projects
        )
    except BootstrapStopped as exc:
        from_env = held is not None and held.from_process_env
        raise click.ClickException(_stopped(exc, config, token_file, rerun, from_env=from_env)) from exc
    except TokenNotWritten as exc:
        raise click.ClickException(
            f"{exc}; retire it with `blizzard hub runner retire {exc.runner_id} --hub-url {hub}`, then re-run "
            f"`{rerun.again()}`"
        ) from exc
    except RunnerAddRefused as exc:
        raise click.ClickException(_add_refused(exc, hub, rerun)) from exc
    except HubClientError as exc:
        raise click.ClickException(f"cannot join the hub at {hub}, so nothing was added: {exc}") from exc
    tenant = _in_tenant(joined.tenant_id, joined.tenant_name)
    if joined.added:
        click.echo(
            f"added runner {joined.runner_name} ({joined.runner_id}) at {hub}{tenant}; its token is in "
            f"{token_file.path}"
        )
        return
    click.echo(f"runner {joined.runner_name} ({joined.runner_id}){tenant} keeps its token for {hub}")
    _restrict(token_file)


def _in_tenant(tenant_id: str | None, tenant_name: str | None) -> str:
    """`` in tenant <name> (<id>)`` for a known tenant, the id alone when its name is unknown, and
    nothing when the tenant itself is unknown."""
    if tenant_id is None:
        return ""
    return f" in tenant {tenant_name} ({tenant_id})" if tenant_name else f" in tenant {tenant_id}"


def _tenant_listing(tenants: tuple[OperatorTenant, ...]) -> str:
    """The tenants an operator may enter, one per line, ids beside names."""
    if not tenants:
        return "\nthe hub listed no tenant you may enter there"
    return "\nthe tenants you may enter:" + "".join(f"\n  {t.tenant_id}  {t.name}" for t in tenants)


def _restrict(token_file: HubTokenFile) -> None:
    """Make a kept ``.env`` owner-only, or warn that another account may read the token it holds."""
    try:
        restricted = token_file.restrict()
    except OSError as exc:
        click.echo(
            f"warning: other accounts may read {token_file.path}, which holds this runner's hub token, and init "
            f"could not make it owner-only ({exc.strerror}); run `chmod 600 {token_file.path}` as its owner"
        )
        return
    if restricted:
        click.echo(f"made {token_file.path} owner-only (0600): it holds this runner's hub token")


def _stopped(
    exc: BootstrapStopped, config: RunnerConfig, token_file: HubTokenFile, rerun: _Rerun, *, from_env: bool
) -> str:
    hub, runner_id = config.hub_url, exc.runner_id or "<id>"
    place = (
        f"set {config.token_env} to it, or unset {config.token_env} and put it in {token_file.path} (init "
        f"presented the token ${config.token_env} carries, which overrides that file)"
        if from_env
        else f"put it in {token_file.path} as {config.token_env}"
    )
    match exc.stop:
        case BootstrapStop.UNKNOWN_TOKEN:
            return (
                f"the hub at {hub} does not know this runner's token, so nothing was added and {token_file.path} "
                f"is unchanged. If that hub's data was deliberately reset, re-run `{rerun.readding()}` "
                f"to add the runner again; otherwise check that {hub} is this runner's hub"
            )
        case BootstrapStop.REVOKED_TOKEN:
            return (
                f"the hub at {hub} has revoked the token of runner {runner_id}, so nothing was added: mint a new "
                f"one with `blizzard hub runner enroll {runner_id} --hub-url {hub}` and {place}"
            )
        case BootstrapStop.RETIRED_RUNNER:
            return (
                f"runner {runner_id} is retired at {hub}, and retiring it revoked its token, so nothing was added: "
                f"reinstate it with `blizzard hub runner reinstate {runner_id} --hub-url {hub}`, then mint it a new "
                f"token with `blizzard hub runner enroll {runner_id} --hub-url {hub}` and {place}"
            )
        case BootstrapStop.TOKEN_NOT_RECEIVED:
            return f"the hub at {hub} received no token from this runner, so nothing was added"
        case BootstrapStop.STORE_NAMES_A_RUNNER:
            return (
                f"this runner holds no token, but its store belongs to runner {runner_id}, so nothing was added: "
                f"put that runner's token in {token_file.path} as {config.token_env}, or — if it is gone for good — "
                f"re-run `{rerun.readding()}` to set its store aside and add a new runner, or clear "
                f"the store with `{reset_command(config.root)}`"
            )
        case BootstrapStop.TOKEN_OVERRIDDEN:
            return (
                f"the hub at {hub} does not know the token ${config.token_env} carries, and a replacement in "
                f"{token_file.path} would go unread while it is set, so nothing was added: unset "
                f"{config.token_env} and re-run `{rerun.again()}`"
            )
        case BootstrapStop.ANOTHER_TENANT:
            return _another_tenant(exc.held, config, token_file, rerun, from_env=from_env)
        case BootstrapStop.TENANT_REQUIRED:
            return (
                f"you may enter several tenants at {hub}, so nothing was added: name the one to add this runner "
                f"in by its id with `{rerun.naming_a_tenant()}`{_tenant_listing(exc.tenants)}"
            )
        case BootstrapStop.NO_SUCH_TENANT:
            return (
                f"the hub at {hub} has no tenant {rerun.tenant_id} you may enter, so nothing was added: name one "
                f"you may enter by its id with `{rerun.naming_a_tenant()}` — a tenant is named by "
                f"its id, never its name{_tenant_listing(exc.tenants)}"
            )


def _another_tenant(
    held: TokenIdentity | None, config: RunnerConfig, token_file: HubTokenFile, rerun: _Rerun, *, from_env: bool
) -> str:
    """The stop for a held token whose runner serves another tenant than ``--tenant`` names: both
    tenants, and the two ways on — keep that runner, or set up a new one in a fresh directory."""
    assert held is not None, "the another-tenant stop carries the held token's runner"
    source = f"the token ${config.token_env} carries" if from_env else f"the token in {token_file.path}"
    serves = _in_tenant(held.tenant_id, held.tenant_name).removeprefix(" in ")
    return (
        f"{source} belongs to runner {held.runner_id}, which serves {serves}, but --tenant names tenant "
        f"{rerun.tenant_id}, so nothing was added: a runner never moves between tenants. Re-run "
        f"`{rerun.keeping_the_runner()}` to keep this runner, or set up a new runner in "
        f"tenant {rerun.tenant_id} from a fresh directory with "
        f"`blizzard runner init <new-directory> --tenant {rerun.tenant_id}`"
    )


def _add_refused(exc: RunnerAddRefused, hub: str, rerun: _Rerun) -> str:
    match exc.reason:
        case RunnerAddRefusalReason.SIGN_IN_REQUIRED:
            return (
                f"the hub at {hub} requires sign-in to add a runner: run `blizzard hub login --hub-url {hub}` "
                f"as an operator holding runner:add, then re-run `{rerun.again()}`"
            )
        case RunnerAddRefusalReason.FORBIDDEN:
            return f"your session at {hub} may not add runners — that takes runner:add: {exc.detail}"
        case RunnerAddRefusalReason.UNSUPPORTED:
            return f"the hub at {hub} predates `hub runner add`; upgrade it, then re-run `{rerun.again()}`"
        case RunnerAddRefusalReason.TENANT_REQUIRED | RunnerAddRefusalReason.NO_SUCH_TENANT:
            return f"the hub at {hub} could not settle the tenant to add this runner in: {exc.detail}"
        case RunnerAddRefusalReason.NO_SUCH_PROJECT:
            return (
                f"the hub at {hub} holds no project answering to every --project named, so nothing was added: "
                f"{exc.detail}"
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
    bundle = _publish_harness_bundle(config)
    for missing in config.missing_worker_path_prepend_entries:
        click.echo(f"warning: [worker] path_prepend entry does not exist: {missing}")
    if config.shadowed_runner_id is not None:
        click.echo(f"warning: legacy runner_id {config.shadowed_runner_id!r} is ignored; name {config.name!r} wins")
    if not config.hub_token:
        env_file = HubTokenFile.of(config.root, config.token_env).path
        click.echo(
            f"warning: no hub token in ${config.token_env} or {env_file}; the hub refuses this runner until"
            " `blizzard runner init` adds it — or, if `blizzard hub runner list` already shows it, put the line"
            f" `blizzard hub runner enroll <id>` prints in {env_file}"
        )
    if config.dropped_otel_passthrough:
        names = ", ".join(config.dropped_otel_passthrough)
        click.echo(f"warning: [worker] env_passthrough OTEL_* names are dropped while tracing is on: {names}")
    with click_exception_on(RevisionMismatchError):
        ensure_current_revision(config)
    # One broker for the process: `host` is the one composer building both the
    # served app and the ticked loop, so every writer and the stream route share it.
    broker = EventBroker()
    identity = RunnerIdentityHolder()
    graph = build_runner_process(
        config,
        events=broker,
        bundle=bundle,
        platform_tracing=build_runner_platform_tracing(config, identity=identity),
        received_telemetry=build_runner_received_telemetry(config),
        identity=identity,
    )
    try:
        hosted = build_hosted_app(config, process_graph=graph)
        try:
            _serve_host(config, graph, hosted)
        finally:
            hosted.close()
    finally:
        graph.close()


def _publish_harness_bundle(config: RunnerConfig) -> BundleSnapshot | None:
    """Load the operator's harness-config bundle and publish its snapshot before any work is
    accepted, returning it for the process graph to deliver; a runtime with no
    `[harness] config_dir` is left untouched."""
    if config.harness_config_dir is None:
        return None
    with click_exception_on(ConfigError):
        snapshot = publish_harness_bundle(
            config.harness_config_dir,
            config.root,
            autonomy=config.autonomy,
            sections=config.harness_sections,
        )
    click.echo(snapshot.summary())
    return snapshot


def _serve_host(config: RunnerConfig, graph: RunnerProcess, hosted: HostedApp) -> None:
    app = hosted.app
    interval = float(os.environ.get(ENV_TICK_SECONDS, DEFAULT_TICK_SECONDS))
    # `PeriodicDriver` resolves its prompt files on this thread, not in the loop thread: a
    # configured-but-missing prompt raises here, before any socket binds.
    with click_exception_on(ConfigError):
        driver = PeriodicDriver(config, interval_seconds=interval, process_graph=graph)
    connections = graph.connections
    rejection = PendingTracingRejection(
        graph.trace_settings, graph.stores.outbound, lambda: connections.runner_id is not None, graph.clock
    )
    rejection.announce()
    pass_drivers = _pass_drivers(config, graph, tick_seconds=interval, rejection=rejection)

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
        for pass_driver in pass_drivers:
            pass_driver.start()
        server.run(sockets=sockets)
    finally:
        try:
            if started:
                # Quiesce before marking; the spawner executor and engine outlive
                # the drain and are closed by the outer host frame.
                for pass_driver in pass_drivers:
                    pass_driver.stop()
                driver.stop()
                marked = hosted.resume.on_shutdown()
                if marked:
                    click.echo(f"marked {marked} in-flight lease(s) for restart-resume")
        finally:
            # uvicorn closes a pre-bound socket but does not unlink its file.
            Uds(config.socket_path).unlink()


def _pass_drivers(
    config: RunnerConfig, graph: RunnerProcess, *, tick_seconds: float, rejection: PendingTracingRejection
) -> list[PeriodicPassDriver]:
    """The off-tick drivers, built only here so `runner tick` never runs a trace sweep or a
    credential renewal. Renewal passes on the tick's own interval with no jitter, so a credential
    lapsed across a restart is renewed at once; each slug's own cadence gates it within the pass.
    A rejected tracing setting whose report the start could not buffer — the store bound to no runner
    yet — retries on the tick's interval until the first registration binds it."""
    drivers: list[PeriodicPassDriver] = []
    if rejection.owed:
        drivers.append(
            PeriodicPassDriver(
                rejection.announce,
                name="blizzard-runner-tracing-rejection",
                label="tracing rejection report",
                log=get_logger("blizzard.runner.trace_export"),
                interval_seconds=tick_seconds,
                jitter_seconds=0,
            )
        )
    if graph.trace_sweep is not None:
        drivers.append(
            PeriodicPassDriver(
                graph.trace_sweep.sweep,
                name="blizzard-runner-trace-sweep",
                label="lease trace sweep",
                log=get_logger("blizzard.runner.trace_export"),
                interval_seconds=config.tracing.sweep_seconds,
            )
        )
    if graph.credential_renewal is not None:
        drivers.append(
            PeriodicPassDriver(
                graph.credential_renewal.run,
                name="blizzard-runner-credential-renewal",
                label="credential renewal pass",
                log=get_logger("blizzard.runner.subscriptions"),
                interval_seconds=tick_seconds,
                jitter_seconds=0,
            )
        )
    return drivers


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
    bundle = _publish_harness_bundle(config)
    with click_exception_on(RevisionMismatchError):
        ensure_current_revision(config)
    LoopWiring.of(config, bundle=bundle).tick_once()
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


@contextmanager
def read_host_stores(config: RunnerConfig) -> Iterator[HostReadStores]:
    """:func:`read_stores`'s host-scoped twin, for a short-lived CLI verb reading a host table."""
    engine = create_engine_from_url(config.db_url)
    try:
        yield build_host_read_stores(engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))
    finally:
        engine.dispose()

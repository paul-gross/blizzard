"""``blizzard runner init``/``migrate``/``host``/``tick`` — scaffold the runtime and join its hub,
migrate, and drive the runtime."""

from __future__ import annotations

import os
import signal
import socket
import types
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from functools import partial
from http import HTTPStatus
from pathlib import Path
from typing import Protocol

import click

from blizzard.cli.collaborators import CliCollaborators, ClientFactory, ConnectError, HTTPError
from blizzard.cli.config_inputs import packaged_flag_registry, strict_config
from blizzard.cli.host_directory import HostDirectory
from blizzard.cli.runtime import (
    bound_port,
    bound_tcp,
    build_early_shutdown_server,
    click_exception_on,
    listening_file_option,
    run_init,
    run_migrate,
)
from blizzard.foundation.clock import IClock
from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.logging import get_logger
from blizzard.foundation.operator_sessions import IReadSessionStore
from blizzard.foundation.operator_sessions.internal.session_file import SessionFile
from blizzard.foundation.roles import dto
from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE, hub_token_variable
from blizzard.foundation.settings_record.refusals import SettingsInvalid
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import (
    MigrationConnectionError,
    MigrationRunner,
    RevisionMismatchError,
    StoreBelowFloorError,
)
from blizzard.foundation.sweep_passes import SweepPassOutcome
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.app import HostedApp, build_hosted_app_over_process
from blizzard.runner.cli.children import command_children
from blizzard.runner.cli.daemon import Door, RunnerDaemon, daemon_holding, runner_option
from blizzard.runner.cli.directory import load_host
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.cli.store import runtime_of
from blizzard.runner.cli.store_guard import (
    host_roster,
    judge_store,
    resolve_runner,
    store_reason,
    store_write_guard,
)
from blizzard.runner.composition import (
    HostProcess,
    IHostedRunnerSource,
    IRunnerDrivers,
    NoDrivers,
    RunnerCompose,
    RunnerProcess,
    RunnerStartFailed,
    SeedingStore,
    StartupNote,
    build_host_process,
    build_host_read_stores,
    build_host_settings_store,
    build_host_stores,
    build_read_stores,
    build_running_host_seeding_store,
    build_seeding_store,
    build_settings_stores,
    host_record_reads,
    recorded_host_config,
    run_startup_steps,
    runner_admission,
    seed_record_tables,
    stored_hub_token,
)
from blizzard.runner.config import (
    DEFAULT_RUNNER_NAME,
    DEFAULT_TICK_SECONDS,
    ENV_TICK_SECONDS,
    RunnerConfig,
    resolve_tick_seconds,
    worker_shell_refusal,
)
from blizzard.runner.config_table import ConfigError
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.binding_selection import BindingSelection
from blizzard.runner.harness.bundle import BundleSnapshot
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.harness.wiring import HarnessSections, publish_harness_bundle, select_harness_bindings
from blizzard.runner.host_config import (
    BuiltRunner,
    HaltedRunner,
    HostConfig,
    HostConfigError,
    ResolvedToken,
    RunnerEntry,
    TokenSource,
)
from blizzard.runner.host_file import write_runner_id
from blizzard.runner.host_layout import (
    DATA_DIRNAME,
    HOST_CONFIG_FILENAME,
    LEGACY_WORKER_SCRATCH_DIRNAME,
    WORKER_SCRATCH_DIRNAME,
    HostLayout,
)
from blizzard.runner.host_root import build_runner_process
from blizzard.runner.host_settings_reader import HostSettingsReader
from blizzard.runner.hosting.model import CLI, JOIN, SEED, HostingFact
from blizzard.runner.hosting.names import stored_name
from blizzard.runner.hub.bootstrap import (
    BootstrapStop,
    BootstrapStopped,
    HeldToken,
    IRunnerStoreAside,
    IStoreSetAside,
    JoinedRunner,
    RunnerBootstrap,
    TokenNotWritten,
    refuse_tokenless,
    refuse_unplaced,
)
from blizzard.runner.hub.client import (
    HostAddRefusalReason,
    HostAddRefused,
    HubClientError,
    IHostHubClient,
    IHostRunnerAdder,
    IHubRunnerAdmin,
    IRunnerAdder,
    ITokenIdentityReader,
    OperatorTenant,
    RunnerAddRefusalReason,
    RunnerAddRefused,
    TokenIdentity,
    TokenRefusal,
)
from blizzard.runner.hub.host_bootstrap import (
    HostBootstrap,
    HostBootstrapStop,
    HostBootstrapStopped,
    HostRunnerAdmin,
    HostTokenNotWritten,
    JoinedHost,
)
from blizzard.runner.hub.identity import IRunnerIdentityStore, judged
from blizzard.runner.hub.internal.http_hub import HttpHostHubClient, HttpHubClient, HttpHubRunnerAdmin
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.hub.sealed_tokens import (
    HubTokens,
    StoredToken,
    StoredTokenState,
    token_set_command,
    unreadable_token,
)
from blizzard.runner.hub.token_seed import RUNNER_SEEDED, import_hub_token
from blizzard.runner.leases.record import IReadLeaseRecordRepository
from blizzard.runner.lifecycle.identity_check import (
    ForeignStoreKind,
    host_store_verdict,
    reset_hazard,
)
from blizzard.runner.listeners import ListenerError, Uds
from blizzard.runner.loop.mode import LoopMode
from blizzard.runner.loop.periodic_pass_driver import PeriodicPassDriver
from blizzard.runner.loop.step_passes import HostPassStepper
from blizzard.runner.loop_wiring import LoopWiring, PeriodicDriver, RunnerStepper, host_pass_stepper
from blizzard.runner.process.child_processes import IChildProcesses
from blizzard.runner.process.internal.scratch_binding_files import ScratchBindingFiles
from blizzard.runner.process.internal.worker_scratch_dirs import WorkerScratchDirs
from blizzard.runner.process.scratch_binding import RunnerScratchLeases, ScratchSweeperFactory, bind_scratch_root
from blizzard.runner.process.scratch_cleanup import ClosedLeaseLookup
from blizzard.runner.runtime import (
    StoreResetRefused,
    ensure_current_revision,
    migration_runner,
    named_store_socket,
    reset_command,
    runner_store_migrations,
)
from blizzard.runner.settings.ports import IWriteRunnerSettings
from blizzard.runner.settings_reader import RunnerSettingsReader
from blizzard.runner.store import MIGRATIONS_DIR
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.stores import HostReadStores, HostStores, RunnerReadStores

#: Bounds each of ``init``'s hub calls, so an unresponsive hub fails the command instead of hanging it.
_INIT_HUB_TIMEOUT_SECONDS = 15.0

type InitHubClients = tuple[ITokenIdentityReader, IHubRunnerAdmin]

#: The host's own hub calls, by the host token each presents.
type HostClients = Callable[[str], IHostHubClient]


@contextmanager
def _http_hub_clients(
    client_factory: ClientFactory, hub_url: str, runner_token: str, operator_token: str | None
) -> Generator[InitHubClients, None, None]:
    """The identity read under the runner's own token, and the add under the operator's session."""
    runner_headers = {"Authorization": f"Bearer {runner_token}"} if runner_token else {}
    with (
        client_factory(base_url=hub_url, timeout=_INIT_HUB_TIMEOUT_SECONDS, headers=runner_headers) as runner_http,
        client_factory(base_url=hub_url, timeout=_INIT_HUB_TIMEOUT_SECONDS) as operator_http,
    ):
        yield HttpHubClient(runner_http), HttpHubRunnerAdmin(operator_http, operator_token=operator_token)


@contextmanager
def _http_host_clients(client_factory: ClientFactory, hub_url: str) -> Generator[HostClients, None, None]:
    """The host's own hub calls, each under the host token it is made with."""
    with client_factory(base_url=hub_url, timeout=_INIT_HUB_TIMEOUT_SECONDS) as http:
        yield lambda token: HttpHostHubClient(http, token=token)


class ISelectRunnerBindings(Protocol):
    """Choosing a runner's bindings, its binaries' versions run through ``children``, as
    :func:`select_runner_bindings` does."""

    def __call__(self, config: RunnerConfig, children: IChildProcesses) -> BindingSelection: ...


class IPublishHarnessBundle(Protocol):
    """Publishing the operator's harness-config bundle, as :func:`publish_harness_bundle` does."""

    def __call__(
        self,
        config_dir: Path,
        runtime_root: Path,
        *,
        selection: BindingSelection,
        autonomy: Autonomy = ...,
        sections: HarnessSections | None = ...,
    ) -> BundleSnapshot: ...


def select_runner_bindings(config: RunnerConfig, children: IChildProcesses) -> BindingSelection:
    """The bindings ``config``'s binaries choose, each version-chosen one's ``--version`` run once through
    ``children``."""
    tool_env = config.worker_env(TracingSettings.of(os.environ)).tool()
    return select_harness_bindings(
        config.harness_sections,
        versions=VersionProbe(children, tool_env),
    )


@dataclass(frozen=True)
class InitCollaborators:
    """The hub clients, store migrations, host-process and standalone builders, and harness-bundle publisher a
    runtime verb acts through: an invoker hands its own to the root group as ``obj``; with none, the HTTP clients
    and the real builders are used."""

    hub_clients: Callable[[str, str, str | None], AbstractContextManager[InitHubClients]]
    host_clients: Callable[[str], AbstractContextManager[HostClients]]
    #: Where the operator's `blizzard hub login` session is read from.
    sessions: IReadSessionStore
    #: The runner store's migrations at a URL.
    store_migrations: Callable[[str], MigrationRunner] = runner_store_migrations
    build_host_process: Callable[..., HostProcess] = build_host_process
    #: The standalone opener a verb driving one runner off any serving host builds its host root and runner through.
    build_runner_process: Callable[..., RunnerProcess] = build_runner_process
    publish_harness_bundle: IPublishHarnessBundle = publish_harness_bundle
    #: The bindings a runner's configured binaries choose, probed once as it starts.
    select_bindings: ISelectRunnerBindings = select_runner_bindings

    @classmethod
    def of(cls, ctx: click.Context) -> InitCollaborators:
        """The invoker's collaborators, or the HTTP ones."""
        return ctx.find_object(InitCollaborators) or cls.http(CliCollaborators.of(ctx).client_factory)

    @classmethod
    def http(cls, client_factory: ClientFactory) -> InitCollaborators:
        return cls(
            partial(_http_hub_clients, client_factory), partial(_http_host_clients, client_factory), SessionFile.of()
        )

    def join_with(self, hub_url: str, token: str) -> AbstractContextManager[InitHubClients]:
        """The identity read and the add for ``hub_url``, under the held ``token`` and the operator's session."""
        return self.hub_clients(hub_url, token, self.sessions.load(hub_url))

    def identity_for(self, hub_url: str, token: str) -> AbstractContextManager[InitHubClients]:
        """The identity read for ``hub_url`` under ``token``, with no operator session."""
        return self.hub_clients(hub_url, token, None)


@click.command()
@click.argument("directory", default=DEFAULT_DIR, envvar=ENV_RUNNER_DIR)
@click.option("--hub", "hub_url", default=None, help="The hub a fresh config names (overrides $BZ_HUB_URL).")
@click.option(
    "--allow-readd",
    is_flag=True,
    help="Add the host, or a runner, again when its hub does not know its token — only after that hub's data was "
    "reset. A host added again sets the store aside and adds every runner again under it; a runner beside another is "
    "never added again in place, since the store it would set aside is theirs too.",
)
@click.option(
    "--tenant",
    "tenant_id",
    default=None,
    metavar="TENANT_ID",
    help="Add the host and its runners in this tenant, by id; needed only when you may enter several.",
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
    """Scaffold the host file + data dir + a migrated store under DIRECTORY, then register the host at its hub and
    join each of its runners under it. Idempotent.

    A host token the hub knows — $BZ_HOST_TOKEN, the host file's `host_token`, then DIRECTORY/.env — keeps its host.
    Holding none, the host is added at the hub under your `blizzard hub login` session when you hold one, in the
    tenant --tenant names or else the only one you may enter, named after its first `[[runner]]`; its token is
    written to DIRECTORY/.env as BZ_HOST_TOKEN. A runner holding no token is added under the host, its token
    written to DIRECTORY/.env as BZ_HUB_TOKEN_<NAME> and its id to its `[[runner]]`; a runner whose token the hub
    knows is kept when it stands under the host. A held token in another tenant than --tenant names stops: neither
    a host nor a runner moves between tenants. A fresh host serves the projects --project names, else `default`. A
    directory a pre-host runner left behind is refused, naming graduation, unless --allow-readd sets its store
    aside. DIRECTORY defaults to $BZ_RUNNER_DIR, then the cwd."""
    named = projects or None
    door = Door.of(ctx)
    clock = CliCollaborators.of(ctx).wall_clock
    initialized: list[HostConfig] = []

    def initialize(root: Path) -> HostConfig:
        runtime = runtime_of(root, door, hub_url=hub_url, projects=named)
        if allow_readd and (aside := runtime.set_aside_pre_host_store(now=clock.now())) is not None:
            for path in aside.moved:
                click.echo(f"set the pre-host store aside as {path}")
        initialized.append(
            runtime.init(partial(InitCollaborators.of(ctx).select_bindings, children=command_children(ctx)))
        )
        return initialized[0]

    run_init(
        Path(directory),
        "runner",
        init_environment=initialize,
        migration_runner=migration_runner,
        config_error=ConfigError,
    )
    (config,) = initialized
    collaborators = InitCollaborators.of(ctx)
    rerun = _Rerun(config.root, tenant_id=tenant_id, allow_readd=allow_readd, projects=named)
    store = _RuntimeStore(config, door, clock)
    for entry in config.runners:
        _refuse_tokenless(config, entry, store, rerun)
    with collaborators.host_clients(config.hub_url) as host_clients:
        host = register_host(config, collaborators, host_clients, store, rerun)
        for entry in config.runners:
            _join_runner(config, entry, collaborators, HostJoin(host, host_clients(host.token)), store, rerun, clock)


@dataclass(frozen=True)
class HostJoin:
    """The host init registered, and its own hub calls under the token it holds."""

    joined: JoinedHost
    client: IHostRunnerAdder


def register_host(
    config: HostConfig,
    collaborators: InitCollaborators,
    host_clients: HostClients,
    store: IStoreSetAside,
    rerun: _Rerun,
) -> JoinedHost:
    """Keep the host the held host token names, or add one named after the first ``[[runner]]`` and write its
    token; any stop exits non-zero with no host added."""
    held_token = config.host_token
    held = _held(held_token)
    token_file = HubTokenFile.of(config.root, HOST_TOKEN_VARIABLE)
    hub = config.hub_url
    name = config.runners[0].name if config.runners else DEFAULT_RUNNER_NAME
    with collaborators.join_with(hub, "") as (_identity, admin):
        adopt = () if held is not None else _adoptable(config, collaborators, rerun)
        try:
            host = HostBootstrap(host_clients, admin, token_file, store).join(
                name, held=held, allow_readd=rerun.allow_readd, tenant_id=rerun.tenant_id, adopt=adopt
            )
        except HostBootstrapStopped as exc:
            raise click.ClickException(_host_stopped(exc, config, token_file, rerun)) from exc
        except HostTokenNotWritten as exc:
            raise click.ClickException(
                f"{exc}; retire it with `blizzard hub runner-host retire {exc.host_id} --hub-url {hub}`, then "
                f"re-run `{rerun.again()}`"
            ) from exc
        except HostAddRefused as exc:
            raise click.ClickException(_host_add_refused(exc, name, hub, rerun)) from exc
        except HubClientError as exc:
            raise click.ClickException(f"cannot register this host at the hub at {hub}: {exc}") from exc
        except (ConfigError, StoreResetRefused) as exc:
            raise click.ClickException(str(exc)) from exc
    record = host.record
    where = f"host {record.host_name} ({record.host_id}) in tenant {record.tenant_id}"
    if host.added:
        again = " in place of the one the hub no longer knows" if host.readded else ""
        click.echo(f"registered {where} at {hub}{again}; its token is in {token_file.path} as {HOST_TOKEN_VARIABLE}")
    else:
        click.echo(f"{where} keeps its token for {hub}")
        if held_token.source is TokenSource.DOTENV:
            _restrict(token_file)
    return host


def _adoptable(config: HostConfig, collaborators: InitCollaborators, rerun: _Rerun) -> tuple[str, ...]:
    """The runners a new host adopts as it is added: each whose held token the hub knows, in the tenant named if
    one is. A token the hub refuses, or a read that fails, adopts nothing — the runner's own join reports it."""
    adopt: list[str] = []
    for entry in config.runners:
        token = held_runner_token(config, entry)
        if not token.value:
            continue
        try:
            with collaborators.identity_for(config.hub_url, token.value) as (identity, _admin):
                answer = identity.identity()
        except HubClientError:
            continue
        if isinstance(answer, TokenIdentity) and answer.serves(rerun.tenant_id):
            adopt.append(answer.runner_id)
    return tuple(dict.fromkeys(adopt))


def _host_stopped(exc: HostBootstrapStopped, config: HostConfig, token_file: HubTokenFile, rerun: _Rerun) -> str:
    hub = config.hub_url
    match exc.stop:
        case HostBootstrapStop.UNKNOWN_HOST_TOKEN:
            return (
                f"the hub at {hub} does not know this host's token, so nothing was added and {token_file.path} is "
                f"unchanged. If that hub's data was deliberately reset, re-run `{rerun.readding()}` to add the host "
                f"and every runner again; otherwise check that {hub} is this host's hub"
            )
        case HostBootstrapStop.HOST_TOKEN_OVERRIDDEN:
            where = (
                f"${HOST_TOKEN_VARIABLE}"
                if config.host_token.source is TokenSource.ENVIRONMENT
                else f"host_token in {HOST_CONFIG_FILENAME}"
            )
            return (
                f"the hub at {hub} does not know the host token {where} carries, and a replacement in "
                f"{token_file.path} would go unread while it is set, so nothing was added: remove it there and "
                f"re-run `{rerun.again()}`"
            )
        case HostBootstrapStop.HOST_RETIRED:
            return (
                f"the hub at {hub} refuses this host's token as a retired host's, so nothing was added: remove "
                f"{HOST_TOKEN_VARIABLE} from where it is set to register a new host, or mint the host a new token "
                f"with `blizzard hub runner-host enroll <host_id> --hub-url {hub}`"
            )
        case HostBootstrapStop.HUB_WITHOUT_HOSTS:
            return f"the hub at {hub} predates hosts, so nothing was added; upgrade it, then re-run `{rerun.again()}`"
        case HostBootstrapStop.ANOTHER_TENANT:
            assert exc.held is not None, "the another-tenant stop carries the held host"
            return (
                f"this host's token belongs to host {exc.held.host_id}, which serves tenant {exc.held.tenant_id}, "
                f"but --tenant names tenant {rerun.tenant_id}, so nothing was added: a host never moves between "
                f"tenants. Re-run `{rerun.keeping_the_runner()}` to keep this host, or set up a new host in tenant "
                f"{rerun.tenant_id} from a fresh directory with "
                f"`blizzard runner init <new-directory> --tenant {rerun.tenant_id}`"
            )
        case HostBootstrapStop.TENANT_REQUIRED:
            return (
                f"you may enter several tenants at {hub}, so nothing was added: name the one to add this host in "
                f"by its id with `{rerun.naming_a_tenant()}`{_tenant_listing(exc.tenants)}"
            )
        case HostBootstrapStop.NO_SUCH_TENANT:
            return (
                f"the hub at {hub} has no tenant {rerun.tenant_id} you may enter, so nothing was added: name one "
                f"you may enter by its id with `{rerun.naming_a_tenant()}` — a tenant is named by "
                f"its id, never its name{_tenant_listing(exc.tenants)}"
            )


def _host_add_refused(exc: HostAddRefused, name: str, hub: str, rerun: _Rerun) -> str:
    match exc.reason:
        case HostAddRefusalReason.SIGN_IN_REQUIRED:
            return (
                f"the hub at {hub} requires sign-in to add a host: run `blizzard hub login --hub-url {hub}` "
                f"as an operator who may add hosts, then re-run `{rerun.again()}`"
            )
        case HostAddRefusalReason.FORBIDDEN:
            return f"your session at {hub} may not add hosts: {exc.detail}"
        case HostAddRefusalReason.UNSUPPORTED:
            return f"the hub at {hub} predates hosts, so nothing was added; upgrade it, then re-run `{rerun.again()}`"
        case HostAddRefusalReason.RUNNER_NOT_FOUND:
            return (
                f"the hub at {hub} could not adopt a runner, so nothing was added: {exc.detail}. "
                f"Check that the runner exists in the selected tenant, then re-run `{rerun.again()}`"
            )
        case HostAddRefusalReason.NAME_TAKEN:
            return (
                f"the hub at {hub} refused to add host {name}, so nothing was added: a live host already holds the "
                f"name, or a runner this host holds a token for stands under another host or is retired "
                f"({exc.detail}). Rename this host's first [[runner]], or retire the other host with "
                f"`blizzard hub runner-host retire <host_id> --hub-url {hub}`"
            )
        case HostAddRefusalReason.TENANT_REQUIRED | HostAddRefusalReason.NO_SUCH_TENANT:
            return f"the hub at {hub} could not settle the tenant to add this host in: {exc.detail}"


def _refuse_tokenless(config: HostConfig, entry: RunnerEntry, store: _RuntimeStore, rerun: _Rerun) -> None:
    """Stop before reaching the hub when ``entry`` holds no token and init would add another runner in place of
    the one it may not replace."""
    token = held_runner_token(config, entry)
    held = _held(token)
    try:
        refuse_tokenless(
            held,
            entry.id,
            None if held is not None else store.stored_runner_id(sibling_of=entry),
            allow_readd=rerun.allow_readd,
        )
    except BootstrapStopped as exc:
        joining, token_file = _joining_of(config, entry, token)
        raise click.ClickException(_stopped(exc, joining, token_file, rerun)) from exc


def _join_runner(
    config: HostConfig,
    entry: RunnerEntry,
    collaborators: InitCollaborators,
    host: HostJoin,
    store: _RuntimeStore,
    rerun: _Rerun,
    clock: IClock,
) -> None:
    """Join ``entry``'s runner to the hub under the host, then write its id into the host file and its identity
    row into the store."""
    token = held_runner_token(config, entry)
    with collaborators.join_with(config.hub_url, token.value) as (identity, _admin):
        joined = _join_hub(
            config,
            entry,
            token,
            identity,
            HostRunnerAdmin(host.client),
            store.for_runner(entry),
            rerun,
            clock,
            host=host.joined,
        )
    # The token is written before the id, and the id before the identity row: a crash between any two is completed
    # by running init again, which keeps the runner the token names and, under --allow-readd, writes the id it
    # left stale. A configured id differing from the joined runner's reaches here only by a re-add.
    if entry.id != joined.runner_id:
        with click_exception_on(ConfigError):
            write_runner_id(config.layout, entry.name, joined.runner_id, strict=config.strict)
    # The identity row and its hosting land together, so a scaffold joined on a hosting store is never judged stray.
    now = clock.now()
    with runner_admission(config.db_url, runner_id=joined.runner_id, clock=clock) as admission:
        admission.record_joined(joined.identity_at(now), HostingFact(actor=JOIN, door=CLI, at=now))


def _refuse_unsettled(config: HostConfig, entry: RunnerEntry, joined: JoinedRunner, clock: IClock) -> None:
    """Stop a kept runner whose store holds a runner neither it nor a sibling ``[[runner]]`` is, before anything is
    written. The configured id is not counted as ours: a store naming a stale one under a re-add is foreign too."""
    siblings = tuple(other.id for other in config.runners if other.name != entry.name and other.id is not None)
    # The joined runner counts among the file's ids: its join writes its hosting with its row.
    roster = host_roster(config, clock)
    verdict = host_store_verdict(roster.named, roster.hosted, (joined.runner_id, *siblings))
    if verdict is None:
        return
    held = f"runner {entry.name}'s token belongs to runner {joined.runner_id}"
    unwritten = "No token, host-file id or identity row was written"
    if verdict.kind is ForeignStoreKind.MIXED:
        raise click.ClickException(
            f"{held}, but the store here names runners {', '.join(verdict.named)}, and "
            f"{', '.join(verdict.foreign)} is configured by no [[runner]] of this host; it cannot be told apart from "
            f"{joined.runner_id}'s, so it is left untouched — stop and restore the right store. {unwritten}"
        )
    hazard = reset_hazard(reset_command(config.root), joined.runner_id)
    raise click.ClickException(
        f"{held}, but the store here names runner {', '.join(verdict.foreign)}; restore the token of "
        f"{verdict.foreign[0]}. {hazard}. {unwritten}"
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
    """The host's own store, as ``init`` sees it while the host is down. It is set aside at most once per init:
    the host added again and each runner added again share the one fresh store."""

    def __init__(self, config: HostConfig, door: Door, clock: IClock) -> None:
        self.config = config
        self.door = door
        self.clock = clock
        self._set_aside = False

    def stored_runner_id(self, *, sibling_of: RunnerEntry | None = None) -> str | None:
        """The runner the store's identity rows name, other than any another ``[[runner]]`` of the host configures
        beside ``sibling_of``, or the store hosts beside the file or holds retired — asked of the cross-runner audit,
        since no runner is bound to a view yet."""
        siblings = {e.id for e in self.config.runners if sibling_of is not None and e.name != sibling_of.name}
        # A runner the store hosts beside the file, or holds retired, is the host's own as a sibling's is: a scaffold
        # joins beside an API-added runner.
        roster = host_roster(self.config, self.clock)
        siblings |= roster.through_a_door() if sibling_of is not None else frozenset()
        named = [runner_id for runner_id in roster.named if runner_id not in siblings]
        return named[0] if named else None

    def is_set_aside(self) -> bool:
        return self._set_aside

    def set_aside(self) -> None:
        if self._set_aside:
            return
        aside = runtime_of(self.config.root, self.door).reset_store(now=self.clock.now())
        self._set_aside = True
        for path in aside.moved:
            click.echo(f"set the old store aside as {path}")

    def for_runner(self, entry: RunnerEntry) -> IRunnerStoreAside:
        """The store as ``entry``'s runner joins it: the runners its siblings configure are theirs, not a former
        owner's."""
        return _RunnerStoreView(self, entry)


@dataclass(frozen=True)
class _RunnerStoreView:
    """The store as one runner joins it: the facts joining judges by, and the move itself."""

    store: _RuntimeStore
    entry: RunnerEntry

    def stored_runner_id(self) -> str | None:
        return self.store.stored_runner_id(sibling_of=self.entry)

    def is_set_aside(self) -> bool:
        return self.store.is_set_aside()

    def sharing(self) -> tuple[str, ...]:
        """The names of the other ``[[runner]]`` entries of the host, which share its store."""
        return tuple(other.name for other in self.store.config.runners if other.name != self.entry.name)

    def set_aside(self) -> None:
        self.store.set_aside()


@dto
@dataclass(frozen=True)
class _Joining:
    """What ``init`` joins: the host's directory and hub, the runner's name and the variable its token is held in."""

    root: Path
    hub_url: str
    name: str
    token_env: str
    #: Where the held token resolved from, in words; ``None`` when ``.env`` supplied it or none is held.
    overridden_by: str | None
    #: The host the runner joins under; ``None`` before the host is registered.
    host_id: str | None = None
    #: Whether the held token is the host store's sealed row, which only ``runner token set`` replaces.
    from_store: bool = False


def _join_hub(
    config: HostConfig,
    entry: RunnerEntry,
    token: ResolvedToken,
    identity: ITokenIdentityReader,
    admin: IRunnerAdder,
    store: IRunnerStoreAside,
    rerun: _Rerun,
    clock: IClock,
    *,
    host: JoinedHost,
) -> JoinedRunner:
    """Keep the runner the held token names when ``host`` holds it, or add one and write its token; any stop exits
    non-zero with nothing added."""
    joining, token_file = _joining_of(config, entry, token, host_id=host.record.host_id)
    hub = config.hub_url
    try:
        joined = RunnerBootstrap(identity, admin, token_file, store).join(
            entry.name,
            held=_held(token),
            allow_readd=rerun.allow_readd,
            tenant_id=rerun.tenant_id,
            projects=rerun.projects,
            configured_id=entry.id,
        )
    except BootstrapStopped as exc:
        raise click.ClickException(_stopped(exc, joining, token_file, rerun)) from exc
    except TokenNotWritten as exc:
        raise click.ClickException(
            f"{exc}; retire it with `blizzard hub runner retire {exc.runner_id} --hub-url {hub}`, then re-run "
            f"`{rerun.again()}`"
        ) from exc
    except RunnerAddRefused as exc:
        raise click.ClickException(_add_refused(exc, hub, rerun)) from exc
    except HubClientError as exc:
        raise click.ClickException(f"cannot join the hub at {hub}, so nothing was added: {exc}") from exc
    except (ConfigError, StoreResetRefused) as exc:
        raise click.ClickException(str(exc)) from exc
    if not joined.added:
        _refuse_unsettled(config, entry, joined, clock)
        try:
            refuse_unplaced(joined, tuple(held.runner_id for held in host.record.runners))
        except BootstrapStopped as exc:
            raise click.ClickException(_stopped(exc, joining, token_file, rerun)) from exc
    tenant = _in_tenant(joined.tenant_id, joined.tenant_name)
    if joined.added:
        click.echo(
            f"added runner {joined.runner_name} ({joined.runner_id}) at {hub}{tenant}; its token is in "
            f"{token_file.path}"
        )
        return joined
    click.echo(f"runner {joined.runner_name} ({joined.runner_id}){tenant} keeps its token for {hub}")
    _restrict(token_file)
    return joined


def _held(token: ResolvedToken) -> HeldToken | None:
    """The token ``token`` resolves to, as joining holds it, or ``None`` when it resolves to none."""
    return HeldToken(token.value, from_process_env=token.source is not TokenSource.DOTENV) if token.value else None


def _joining_of(
    config: HostConfig, entry: RunnerEntry, token: ResolvedToken, *, host_id: str | None = None
) -> tuple[_Joining, HubTokenFile]:
    """What ``init`` joins for ``entry``, and the ``.env`` file its token is written to."""
    token_env = hub_token_variable(entry.name)
    joining = _Joining(
        root=config.root,
        hub_url=config.hub_url,
        name=entry.name,
        token_env=token_env,
        overridden_by=None if token.source is TokenSource.DOTENV else _source_words(token, token_env, entry.name),
        host_id=host_id,
        from_store=token.source is TokenSource.STORE,
    )
    return joining, HubTokenFile.of(config.root, token_env)


def _source_words(token: ResolvedToken, token_env: str, name: str) -> str:
    """Where ``token`` came from, in words."""
    if token.source is TokenSource.STORE:
        return f"the host store, set with {token_set_command(name)}"
    if token.source is TokenSource.HOST_FILE:
        return f"[hub_tokens] in {HOST_CONFIG_FILENAME}"
    if token.source is TokenSource.DOTENV:
        return f"{token_env} in .env"
    return f"${token_env}"


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


def _stopped(exc: BootstrapStopped, joining: _Joining, token_file: HubTokenFile, rerun: _Rerun) -> str:
    hub, runner_id, token_env = joining.hub_url, exc.runner_id or "<id>", joining.token_env
    place = (
        f"replace it where init found it ({joining.overridden_by}), which overrides {token_file.path}"
        if joining.overridden_by is not None
        else (
            f"put it in {token_file.path} as {token_env} (read only when the host store holds no token for the runner; "
            f"otherwise set it with `blizzard runner token set --runner {joining.name}`)"
        )
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
        case BootstrapStop.TOKEN_REFUSED:
            return f"the hub at {hub} refused this runner's token ({exc.reason}), so nothing was added"
        case BootstrapStop.STORE_NAMES_A_RUNNER:
            return (
                f"this runner holds no token, but its store belongs to runner {runner_id}, so nothing was added: "
                f"put that runner's token in {token_file.path} as {token_env}, or — if it is gone for good — "
                f"re-run `{rerun.readding()}` to set its store aside and add a new runner, or clear "
                f"the store with `{reset_command(joining.root)}`"
            )
        case BootstrapStop.TOKEN_OVERRIDDEN if joining.from_store:
            return (
                f"the hub at {hub} does not know the token sealed in the host store, which init never replaces, so "
                f"nothing was added: set the runner's new token with `blizzard runner token set --runner "
                f"{joining.name}` and re-run `{rerun.again()}`"
            )
        case BootstrapStop.TOKEN_OVERRIDDEN:
            return (
                f"the hub at {hub} does not know the token {joining.overridden_by} carries, and a replacement in "
                f"{token_file.path} would go unread while it is set, so nothing was added: remove it there "
                f"and re-run `{rerun.again()}`"
            )
        case BootstrapStop.ANOTHER_TENANT:
            return _another_tenant(exc.held, joining, token_file, rerun)
        case BootstrapStop.ANOTHER_RUNNER:
            assert exc.held is not None, "the another-runner stop carries the held token's runner"
            return (
                f"runner {joining.name} is configured as {runner_id}, but its token belongs to runner "
                f"{exc.held.runner_id}; restore the token of {runner_id} (or correct the `id` in the host file), or "
                "add a new runner in its place with --allow-readd. No token, host-file id or identity row was written"
            )
        case BootstrapStop.TOKENLESS_CONFIGURED:
            return (
                f"runner {joining.name} is configured as {runner_id} but holds no token, so init would add another "
                f"runner in its place; restore its token, or re-run `{rerun.readding()}` to set its store aside and "
                "add a new one"
            )
        case BootstrapStop.NOT_UNDER_HOST:
            return (
                f"runner {joining.name}'s token belongs to runner {runner_id}, which the hub at {hub} does not hold "
                f"under this host ({joining.host_id}), so its id and identity row were not written: place it under "
                f"this host with `blizzard hub runner-host adopt {joining.host_id} {runner_id} --hub-url {hub}`, "
                f"then re-run `{rerun.again()}`"
            )
        case BootstrapStop.STORE_SHARED:
            name, root = joining.name, joining.root
            return (
                f"runner {name} cannot be added again in place: the store it would set aside also holds runner "
                f"{', '.join(exc.sharing)}, whose backlog it would drop. Retire it with `blizzard runner retire "
                f"--runner {name} --dir {root}` and add a fresh one with `blizzard runner add {name} --project "
                f"<project> --dir {root}`. Nothing was added"
            )


def _another_tenant(held: TokenIdentity | None, joining: _Joining, token_file: HubTokenFile, rerun: _Rerun) -> str:
    """The stop for a held token whose runner serves another tenant than ``--tenant`` names: both
    tenants, and the two ways on — keep that runner, or set up a new one in a fresh directory."""
    assert held is not None, "the another-tenant stop carries the held token's runner"
    source = (
        f"the token {joining.overridden_by} carries" if joining.overridden_by else f"the token in {token_file.path}"
    )
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
        case RunnerAddRefusalReason.UNSUPPORTED:
            return f"the hub at {hub} predates `hub runner add`; upgrade it, then re-run `{rerun.again()}`"
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
    help="Host directory (overrides $BZ_RUNNER_DIR).",
)
@click.option("--down", default=None, help="Reverse migrations down to this revision (e.g. base).")
@click.option("--head", is_flag=True, help="Print this build's runner migration head without opening a store.")
@click.option("--knows", metavar="REV", help="Exit successfully if this build holds REV; print nothing.")
@click.pass_context
def migrate_cmd(ctx: click.Context, directory: str, down: str | None, head: bool, knows: str | None) -> None:
    """Apply or reverse migrations, or inspect this build's migration tree.

    A directory that has not graduated to a host is refused and left as it is. A graduated one holding a pre-host
    `data/runner.db` and no `data/host.db` adopts that store first. Adoption and --down hold the directory and are
    refused while a host is running; a plain upgrade is refused only while a pre-host daemon answers."""
    door = Door.of(ctx)

    def migrate(root: Path, down: str | None) -> None:
        runtime_of(root, door).migrate(down=down)

    run_migrate(
        Path(directory),
        down,
        head=head,
        knows=knows,
        script_location=MIGRATIONS_DIR,
        migrate=migrate,
        config_error=ConfigError,
    )


@click.command()
@click.argument("directory", required=False, default=None)
@click.option(
    "--dir",
    "dir_option",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Host directory (overrides $BZ_RUNNER_DIR).",
)
@click.option("--host", "host_", default=None, help="Bind host (overrides config).")
@click.option("--port", type=int, default=None, help="Bind port (overrides config).")
@click.option(
    "--loop",
    "loop",
    type=click.Choice([str(mode) for mode in LoopMode]),
    default=None,
    help="Run passes on timers (timed) or only on request (stepped); overrides the host file's `loop` for this run.",
)
@click.option(
    "--check",
    is_flag=True,
    help="Run every check a start would — the host file, the store's revision, each runner's token resolved to its "
    "id — report each error, and start nothing.",
)
@listening_file_option
@click.pass_context
def host(
    ctx: click.Context,
    directory: str | None,
    dir_option: str,
    host_: str | None,
    port: int | None,
    loop: str | None,
    check: bool,
    listening_file: Path | None,
) -> None:
    """Run the blizzard host until stopped: each runner works its chunks, and its local API answers `runner` verbs.

    DIRECTORY (positional) and --dir are equivalent — pass one; giving both requires
    they agree. Defaults to $BZ_RUNNER_DIR, then the cwd. A directory that has not graduated to a host is
    refused, naming the graduation procedure."""
    directory = HostDirectory(directory, dir_option).path
    loop_mode = LoopMode(loop) if loop is not None else None
    if check:
        _check(ctx, Path(directory), host=host_, port=port, loop=loop_mode)
        return
    config = load_host(directory, host=host_, port=port, loop=loop_mode, defer_tool_agreement=True)
    # Bound before anything is built, so every derivation of the local API's URL sees the port actually bound.
    with bound_tcp(config.host, config.port) as sock:
        config = replace(config, port=bound_port(sock))
        for warning in config.warnings:
            click.echo(f"warning: {warning}")
        entries = [entry for entry in config.runners if entry.id is not None]
        halted = [
            projection
            for projection in config.projections()
            if isinstance(projection, HaltedRunner) and projection.id is None
        ]
        clock = CliCollaborators.of(ctx).wall_clock
        with store_write_guard(
            config, action="start the host", door=Door.of(ctx), clock=clock, refuse_foreign=False
        ) as verdict:
            config = seed_host(config, os.environ, clock=clock)
            if verdict.foreign is not None:
                # A store that is not the host's is left untouched: every runner the file names halts with the reason.
                halted += [
                    HaltedRunner(name=entry.name, id=entry.id, errors=(store_reason(verdict, entry.id) or "",))
                    for entry in entries
                    if entry.id is not None
                ]
                seeded: list[BuiltRunner] = []
            else:
                seeded, seed_halted = seed_runners(config, clock=clock, records_seeded=True)
                halted += seed_halted
            start(
                config,
                seeded,
                halted,
                InitCollaborators.of(ctx),
                tcp_socket=sock,
                listening_file=listening_file,
                store_is_hosts=verdict.foreign is None,
            )


def seed_host(config: HostConfig, environ: Mapping[str, str], *, clock: IClock) -> HostConfig:
    """Seed the host's record from its file, in one write, then its workspaces and subscriptions,
    after the store guard has passed and before any runner is built; returns the host configuration the record and
    records resolve to. A key the store holds an entry for, an unset
    included, is never touched. ``tick_seconds`` falls through to the deprecated variable where neither the store nor
    the file states one. A file with no ``[tools]`` seeds the profile its runners agree on, while it is not empty."""
    engine = create_engine_from_url(config.db_url)
    try:
        stores = build_host_stores(
            engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), clock=clock
        )
        store = stores.settings
        reader = HostSettingsReader(config, store, host_record_reads(stores))
        held = store.snapshot().entries
        tools = tuple(key for key in reader.keys if key.startswith("tools."))
        if config.tool_profile_conflict is not None and not any(key in held for key in tools):
            raise click.ClickException(f"host: {config.tool_profile_conflict}")
        try:
            tick = resolve_tick_seconds(stored=held.get("tick_seconds"), stated=config.tick_seconds, environ=environ)
        except ValueError as exc:
            raise click.ClickException(f"host: {ENV_TICK_SECONDS} is not a number of seconds: {exc}") from exc
        source = deepcopy(dict(config.authored or {}))
        if tick.seed is not None:
            source["tick_seconds"] = tick.seed
        profile = (config.tool_path_prepend, config.tool_env_passthrough)
        if "tools" not in source and config.tool_profile_conflict is None and any(profile):
            source["tools"] = {"path_prepend": list(profile[0]), "env_passthrough": list(profile[1])}
        try:
            store.seed(ConfigView.unchecked(source), tuple(reader.keys), validate=reader.validate)
            seed_record_tables(config, stores, clock=clock)
            _, effective = reader.snapshot()
        except (SettingsInvalid, ValueError) as exc:
            raise click.ClickException(f"host: {exc}") from exc
    finally:
        engine.dispose()
    if tick.warning is not None:
        click.echo(f"warning: {tick.warning}")
    return effective


def seed_host_records(config: HostConfig, *, clock: IClock) -> HostConfig:
    """Seed the host's workspaces and subscriptions alone, for a stopped-host verb that judges them under its
    store guard: each ``[[workspace]]`` and ``[[subscription]]`` the store holds no record of. Returns ``config``
    with the workspaces and subscriptions the store's records then state (:func:`recorded_host`)."""
    engine = create_engine_from_url(config.db_url)
    try:
        stores = build_host_stores(
            engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), clock=clock
        )
        seed_record_tables(config, stores, clock=clock)
        return recorded_host(config, stores)
    finally:
        engine.dispose()


def recorded_host(config: HostConfig, stores: HostStores | HostReadStores) -> HostConfig:
    """``config`` with its workspaces and subscriptions the store's active records, over its stored entries — what a
    verb judges a workspace or subscription against, never the file's tables."""
    try:
        return recorded_host_config(config, stores)
    except ValueError as exc:
        raise click.ClickException(f"host: {exc}") from exc


def read_recorded_host(config: HostConfig) -> HostConfig:
    """:func:`recorded_host` read from the store with no write, for a running host, which seeded at its start."""
    with read_host_stores(config) as stores:
        return recorded_host(config, stores)


def seed_hosted_runners(config: HostConfig, *, clock: IClock) -> None:
    """Seed step 4, once the store is judged the host's: every ``[[runner]]`` with an id the store neither hosts nor
    holds retired gets its hosted row and ``added`` fact, all in one transaction, so no crash leaves a hosting store
    with an identity row and no hosted row. A scaffold is hosted by its join. An entry naming a runner the store holds
    retired is never started, and is warned about."""
    engine = create_engine_from_url(config.db_url)
    try:
        hosted = build_host_stores(
            engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), clock=clock
        ).hosted_runners
        retired = hosted.retired()
        hosted.host(
            [entry.id for entry in config.runners if entry.id is not None and entry.id not in retired],
            HostingFact(actor=SEED, door=SEED, at=clock.now()),
        )
    finally:
        engine.dispose()
    for entry in config.runners:
        if entry.id is not None and entry.id in retired:
            click.echo(
                f"warning: runner {entry.name} ({entry.id}) is retired; its `[[runner]]` is no longer read — remove it"
            )


def hosted_entries(config: HostConfig, *, clock: IClock) -> list[RunnerEntry]:
    """The entry of every runner the store hosts and has not retired: the ``[[runner]]`` naming it, in file order, then
    each the file does not name, in the order it was hosted, built from its stored ``name``. Every runner's binding is
    made from ``hosted_runners``, never from the file's ``id``."""
    engine = create_engine_from_url(config.db_url)
    errors = RunnerStoreErrorFactory(get_logger("blizzard.runner.store"))
    try:
        active = build_host_stores(engine, errors=errors, clock=clock).hosted_runners.active()
        named = {entry.id: entry for entry in config.runners if entry.id is not None}
        entries = [named[runner_id] for runner_id in named if runner_id in active]
        for runner_id in active:
            if runner_id not in named:
                identity, settings = build_settings_stores(engine, errors=errors, runner_id=runner_id)
                entries.append(config.hosted_entry(runner_id, stored_name(identity, settings, runner_id)))
    finally:
        engine.dispose()
    return entries


def seed_runners(
    config: HostConfig, *, clock: IClock, records_seeded: bool = False
) -> tuple[list[BuiltRunner], list[HaltedRunner]]:
    """Host the file's runners, then seed and resolve the store's active hosted ones, after the store guard has
    passed: each runner's token is imported into the store first, then its settings are seeded, then it
    is projected over its stored token. Mints the host's key when it holds none. The host's workspaces and
    subscriptions are seeded first unless ``records_seeded`` — :func:`seed_host` seeded them — so each runner
    resolves against the host's records."""
    if not records_seeded:
        seed_host_records(config, clock=clock)
    seed_hosted_runners(config, clock=clock)
    ready: list[BuiltRunner] = []
    halted: list[HaltedRunner] = []
    entries = hosted_entries(config, clock=clock)
    engine = create_engine_from_url(config.db_url)
    try:
        store = build_seeding_store(
            engine,
            config.root,
            errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")),
            environ=os.environ,
            clock=clock,
        )
        projections = _seed_entries(config, store, entries, RUNNER_SEEDED.reached)
    finally:
        engine.dispose()
    for projection in projections:
        if isinstance(projection, HaltedRunner):
            halted.append(projection)
        else:
            ready.append(projection)
    return ready, halted


def _seed_entries(
    config: HostConfig,
    store: SeedingStore,
    entries: Sequence[RunnerEntry],
    after_each: Callable[[], None] | None = None,
) -> list[BuiltRunner | HaltedRunner]:
    """Each hosted runner's token imported, its settings seeded, and the runner projected over its stored token, in
    ``store``, which the caller owns; ``after_each`` runs once each is seeded."""
    errors = RunnerStoreErrorFactory(get_logger("blizzard.runner.store"))
    projections: list[BuiltRunner | HaltedRunner] = []
    host_settings = HostSettingsReader(config, store.host_stores.settings, host_record_reads(store.host_stores))
    with click_exception_on(ConfigError):
        tokens = store.tokens()
    for entry in entries:
        assert entry.id is not None
        stored = _import_token(config, entry, tokens)
        identity, settings = build_settings_stores(store.engine, errors=errors, runner_id=entry.id)
        projections.append(_seed_runner(config, entry, stored, identity, settings, host_settings))
        if after_each is not None:
            after_each()
    return projections


@dataclass(frozen=True)
class StoreHostedRunners:
    """The running host's source of a runner the store hosts, seeded as a start seeds it: the ``[[runner]]`` naming
    it, else its stored ``name``, its token imported and its settings seeded at the tick that starts it, over the
    running host's own store."""

    config: HostConfig
    store: SeedingStore

    @classmethod
    def over(cls, config: HostConfig, host: HostProcess) -> StoreHostedRunners:
        return cls(config, build_running_host_seeding_store(host, config.root, environ=os.environ))

    def built(self, runner_id: str) -> BuiltRunner | HaltedRunner:
        entry = next((entry for entry in self.config.runners if entry.id == runner_id), None)
        if entry is None:
            identity, settings = build_settings_stores(
                self.store.engine,
                errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")),
                runner_id=runner_id,
            )
            entry = self.config.hosted_entry(runner_id, stored_name(identity, settings, runner_id))
        try:
            (projection,) = _seed_entries(self.config, self.store, [entry])
        except click.ClickException as exc:
            return HaltedRunner(entry.name, runner_id, (exc.message,))
        return projection


_hosted_runner_source_conforms: type[IHostedRunnerSource] = StoreHostedRunners


def _import_token(config: HostConfig, entry: RunnerEntry, tokens: HubTokens) -> StoredToken:
    """One runner's deprecated-source token sealed unless it holds a row, warning when its row does not open or a
    deprecated source still holds a token the host no longer reads."""
    assert entry.id is not None
    resolved = config.token_for(entry)
    outcome = import_hub_token(tokens, entry.id, resolved.value)
    stored = outcome.stored
    remedy = f"set it with {token_set_command(entry.name)}"
    if stored.state is StoredTokenState.UNREADABLE:
        click.echo(
            f"warning: runner {entry.name} ({entry.id}): {unreadable_token(stored.key_id)}, so it refuses until its "
            f"token is set again; {remedy}"
        )
    elif stored.state is StoredTokenState.OPENED and not outcome.imported and resolved.value:
        click.echo(
            f"warning: runner {entry.name} ({entry.id}): its hub token is sealed in the host store, so the token "
            f"{_source_words(resolved, hub_token_variable(entry.name), entry.name)} still holds is no longer read; "
            f"to change it, {remedy}, then remove it there"
        )
    return stored


def _seed_runner(
    config: HostConfig,
    entry: RunnerEntry,
    stored: StoredToken,
    identity: IRunnerIdentityStore,
    settings: IWriteRunnerSettings,
    host_settings: HostSettingsReader,
) -> BuiltRunner | HaltedRunner:
    """One runner's settings seeded, then the runner projected over its stored token."""
    assert entry.id is not None
    reader = RunnerSettingsReader(config, entry, settings, host_settings=host_settings)
    try:
        if identity.runner_identity() is None:
            # An adopted runner first obtains its identity from registration. Use its authored configuration until
            # that happens; do not persist settings without the identity that owns them.
            resolved = reader.preview_seed()
        else:
            reader.seeder(settings).run()
            _, resolved = reader.snapshot()
    except (SettingsInvalid, ValueError) as exc:
        return HaltedRunner(entry.name, entry.id, (str(exc),))
    projection = config.project(replace(entry, own=resolved, errors=()), stored, os.environ)
    if isinstance(projection, HaltedRunner):
        return projection
    return replace(projection, config=replace(resolved, hub_token=stored.value), entry=entry)


def held_runner_token(config: HostConfig, entry: RunnerEntry) -> ResolvedToken:
    """The token ``entry``'s runner holds: its stored row's, else what its deprecated sources hold. A row that does
    not open stops the verb, naming the remedy, rather than reading as no token. Never mints the host's key."""
    if entry.id is not None:
        stored = _stored_token(config, entry.id)
        if stored.state is StoredTokenState.OPENED:
            return ResolvedToken(stored.value, TokenSource.STORE)
        if stored.state is StoredTokenState.UNREADABLE:
            raise click.ClickException(
                f"runner {entry.name} ({entry.id}): {unreadable_token(stored.key_id)}; set it again with "
                f"{token_set_command(entry.name)}, or supply the key it was sealed under"
            )
    return config.token_for(entry)


def _stored_token(config: HostConfig, runner_id: str) -> StoredToken:
    """``runner_id``'s stored-token outcome, read without minting the host's key."""
    engine = create_engine_from_url(config.db_url)
    try:
        with click_exception_on(ConfigError):
            return stored_hub_token(engine, config.root, runner_id, environ=os.environ)
    finally:
        engine.dispose()


def start(
    config: HostConfig,
    runners: Sequence[BuiltRunner],
    halted: Sequence[HaltedRunner],
    collaborators: InitCollaborators,
    *,
    tcp_socket: socket.socket | None = None,
    listening_file: Path | None = None,
    store_is_hosts: bool = False,
) -> None:
    """Start the host over its built runners, reporting each halted one; returns when the host has stopped.
    ``tcp_socket`` is the TCP listener already bound at ``config``'s port; ``listening_file`` receives the local
    API's URL once the host is serving. With ``store_is_hosts``, the host's tick starts each runner the store comes to
    host and drains each it comes to hold retired."""
    for runner in halted:
        click.echo(f"warning: {runner.describe()}")
    for built in runners:
        if refusal := worker_shell_refusal(built.config.worker_shell):
            raise click.ClickException(f"runner {built.config.name}: {refusal}")
    tracing = TracingSettings.of(os.environ)
    for built in runners:
        for missing in built.config.missing_worker_path_prepend_entries:
            click.echo(f"warning: runner {built.config.name}: [worker] path_prepend entry does not exist: {missing}")
        if dropped := built.config.dropped_otel_passthrough(tracing):
            names = ", ".join(dropped)
            click.echo(
                f"warning: runner {built.config.name}: [worker] env_passthrough OTEL_* names are dropped while "
                f"tracing is on: {names}"
            )
    # One broker for the process: `host` is the one composer building both the
    # served app and the ticked loops, so every writer and the stream route share it.
    broker = EventBroker()
    with click_exception_on(ConfigError):
        process = collaborators.build_host_process(
            config, halted=halted, events=broker, trace_platform=True, tcp_socket=tcp_socket
        )
    try:
        # The app is built over the host's own keys, never a runner's.
        shared = config.host_view()
        source: IHostedRunnerSource | None = StoreHostedRunners.over(config, process) if store_is_hosts else None
        # A runner's bad entry, or a configured-but-missing prompt file, raises while the app composes and wires its
        # runners, before any socket binds. The boot publishes each runner's harness bundle; a later start does not.
        with click_exception_on(ConfigError):
            hosted = build_hosted_app_over_process(
                shared,
                process,
                host_config=config,
                source=source,
                boot=runners,
                compose=RunnerCompose(publish=frozenset(built.id for built in runners)),
            )
        for live in hosted.runners.live():
            if snapshot := live.runner.harness_sets.current.bundle:
                click.echo(snapshot.summary())
        # The host's record resolved into ``config`` at its seed: its entry, else the file's, else the default.
        tick_seconds = config.tick_seconds if config.tick_seconds is not None else DEFAULT_TICK_SECONDS
        try:
            _serve_host(
                shared, process, hosted, loop=config.loop, tick_seconds=tick_seconds, listening_file=listening_file
            )
        finally:
            hosted.close()
    finally:
        process.close()


def _check(ctx: click.Context, root: Path, *, host: str | None, port: int | None, loop: LoopMode | None) -> None:
    """``host --check``: every error a start would meet, reported together; starts nothing and holds nothing."""
    try:
        config = HostConfig.load_for_store(
            root,
            host=host,
            port=port,
            loop=loop,
            flag_registry=packaged_flag_registry(),
            strict=strict_config(),
            defer_tool_agreement=True,
        )
    except HostConfigError as exc:
        raise click.ClickException("\n".join(f"host: {error}" for error in exc.errors)) from exc
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    for warning in config.warnings:
        click.echo(f"warning: {warning}")
    problems: list[str] = []
    named_socket = named_store_socket(config)
    if named_socket is not None and (held := daemon_holding(named_socket, Door.of(ctx))) is not None:
        problems.append(
            f"store: {held}, and it holds the store {config.db_url} the host file names; "
            "stop it, or drop the `db_url` so the host's own store applies"
        )
    try:
        ensure_current_revision(config)
        click.echo(f"store: {config.db_url} is at the revision this build expects")
        verdict = judge_store(config, CliCollaborators.of(ctx).wall_clock)
        current = True
    except (RevisionMismatchError, MigrationConnectionError, StoreBelowFloorError) as exc:
        problems.append(f"store: {exc}")
        verdict = None
        current = False
    collaborators = InitCollaborators.of(ctx)
    engine = create_engine_from_url(config.db_url) if current else None
    try:
        # The runners' disagreeing tool profiles are refused only while the store holds no host profile of its own.
        if config.tool_profile_conflict is not None:
            stored_keys: Sequence[str] = ()
            if engine is not None:
                errors = RunnerStoreErrorFactory(get_logger("blizzard.runner.store"))
                stored_keys = tuple(build_host_settings_store(engine, errors=errors).snapshot().entries)
            if not any(key.startswith("tools.") for key in stored_keys):
                problems.append(f"host: {config.tool_profile_conflict}")
        for entry in config.runners:
            sealed = False
            if entry.id is not None and current:
                problem = store_reason(verdict, entry.id) if verdict is not None else None
                if problem:
                    problems.append(f"runner {entry.name} ({entry.id}): {problem}")
                    continue
                assert engine is not None
                _, settings = build_settings_stores(
                    engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), runner_id=entry.id
                )
                try:
                    resolved = RunnerSettingsReader(config, entry, settings).preview_seed()
                except (SettingsInvalid, ValueError) as exc:
                    problems.append(HaltedRunner(entry.name, entry.id, (str(exc),)).describe())
                    continue
                # A preview: the row when there is one, otherwise what the seed would import — nothing is written.
                with click_exception_on(ConfigError):
                    stored = stored_hub_token(engine, config.root, entry.id, environ=os.environ)
                sealed = stored.state is not StoredTokenState.ABSENT
                if not sealed:
                    stored = config.token_for(entry).importing()
                projection = config.project(replace(entry, own=resolved, errors=()), stored, os.environ)
            else:
                projection = config.project(entry, config.token_for(entry).importing(), os.environ)
            if isinstance(projection, HaltedRunner):
                problems.append(projection.describe())
                continue
            if projection.token.state is StoredTokenState.UNREADABLE:
                problems.append(
                    f"runner {projection.config.name} ({projection.id}): {unreadable_token(projection.token.key_id)}; "
                    f"set it again with {token_set_command(projection.config.name)}"
                )
                continue
            problem = _resolve_token(collaborators, config.hub_url, projection)
            if problem is None:
                where = "sealed in the host store" if sealed else "which the next start imports"
                click.echo(f"runner {projection.config.name} ({projection.id}): its token ({where}) resolves to its id")
            else:
                problems.append(f"runner {projection.config.name} ({projection.id}): {problem}")
    finally:
        if engine is not None:
            engine.dispose()
    if problems:
        raise click.ClickException("\n".join(problems))
    click.echo("host ok")


def _resolve_token(collaborators: InitCollaborators, hub_url: str, built: BuiltRunner) -> str | None:
    """Why ``built``'s token does not resolve to its id at the hub, or ``None`` when it does."""
    try:
        with collaborators.identity_for(hub_url, built.config.hub_token) as (identity, _admin):
            answer = identity.identity()
    except HubClientError as exc:
        return f"cannot resolve its token at the hub {hub_url}: {exc}"
    if isinstance(answer, TokenRefusal):
        return f"the hub at {hub_url} refuses its token ({answer.reason})"
    if judged(built.id, answer.runner_id) is IdentityState.MISMATCHED:
        return f"its token resolves to runner {answer.runner_id}, not the configured {built.id}"
    return None


def publish_runner_bundle(
    config: RunnerConfig, collaborators: InitCollaborators, selection: BindingSelection
) -> BundleSnapshot | None:
    """Load the operator's harness-config bundle and publish its snapshot before any work is
    accepted, returning it for the process graph to deliver; a runtime with no
    `[harness] config_dir` is left untouched. ``selection`` is the bindings the runner composes."""
    if config.harness_config_dir is None:
        return None
    with click_exception_on(ConfigError):
        snapshot = collaborators.publish_harness_bundle(
            config.harness_config_dir,
            config.root,
            autonomy=config.autonomy,
            sections=config.harness_sections,
            selection=selection,
        )
    click.echo(snapshot.summary())
    return snapshot


@dataclass(frozen=True)
class _RunnerThreads:
    """One runner's serving threads: its reconciliation driver and the off-tick drivers beside it."""

    runner: RunnerProcess
    driver: PeriodicDriver
    pass_drivers: list[PeriodicPassDriver]

    def start(self) -> None:
        self.driver.start()
        for pass_driver in self.pass_drivers:
            pass_driver.start()

    def stop(self) -> None:
        for pass_driver in self.pass_drivers:
            pass_driver.stop()
        self.driver.stop()


def tick_interval(host: HostProcess, tick_seconds: float) -> Callable[[], float]:
    """The live tick interval: ``tick_seconds`` at the host record's current revision, else the boot value on a
    host built from no host file."""
    if host.host_settings is None:
        return lambda: tick_seconds
    return host.host_settings.reader.live(
        lambda config: config.tick_seconds if config.tick_seconds is not None else DEFAULT_TICK_SECONDS
    )


def sweep_interval(host: HostProcess, stepper: RunnerStepper) -> Callable[[], float]:
    """The live trace-sweep interval: ``tracing.sweep_seconds`` at the host record's current revision, else the
    runner's last sweep's on a host built from no host file."""
    if host.host_settings is None:
        return stepper.trace_interval
    return host.host_settings.reader.live(lambda config: config.tracing.sweep_seconds)


def build_runner_threads(
    host: HostProcess, runner: RunnerProcess, stepper: RunnerStepper, *, tick_seconds: float
) -> _RunnerThreads:
    """One runner's timed drivers, each waiting its live interval on its own wake signal."""
    driver = PeriodicDriver(
        runner.config,
        interval=tick_interval(host, tick_seconds),
        process_graph=runner,
        stepper=stepper,
        signal=host.wakes.signal(),
        clock=host.monotonic_clock,
    )
    return _RunnerThreads(runner, driver, _pass_drivers(host, runner, stepper))


def _serve_host(
    config: RunnerConfig,
    host: HostProcess,
    hosted: HostedApp,
    *,
    loop: LoopMode,
    tick_seconds: float,
    listening_file: Path | None = None,
) -> None:
    """Serve ``hosted`` until stopped. A ``timed`` loop starts every pass on its timer, each runner's tick and the
    host tick waiting the live ``tick_seconds`` (``tick_seconds`` here is its boot value) between passes; a ``stepped``
    one starts none, and a pass runs only when
    requested through its stepper. A runner the host tick starts later is driven the same way."""
    app = hosted.app
    interval = tick_seconds
    stepped = loop is LoopMode.STEPPED

    def drivers(runner: RunnerProcess, stepper: RunnerStepper) -> IRunnerDrivers:
        return NoDrivers() if stepped else build_runner_threads(host, runner, stepper, tick_seconds=interval)

    def no_scratch(_runner: RunnerProcess) -> None:
        return None

    def own_scratch(runner: RunnerProcess) -> None:
        _reconcile_host_scratch(config, host, [runner.runner_id], every_named=False)

    # The boot's runners share the one host-wide scratch reconcile below; a runner started later reconciles its own.
    hosted.start.serving(drivers=drivers, scratch=no_scratch, said=_echo_note)
    host_tick = (
        None
        if stepped or hosted.host_stepper is None
        else host_tick_driver(host, interval=tick_interval(host, interval), stepper=hosted.host_stepper)
    )

    # Two doors onto the one app, bound up front so a clash fails startup loudly and
    # served by the single `Server` below, which keeps the shutdown path on one frame.
    with click_exception_on(ListenerError):
        sockets = host.listeners.bound()
    cadence = "stepped: a pass runs only on request" if stepped else f"loop tick {interval}s"
    click.echo(f"serving blizzard-runner on {config.host}:{config.port} and {config.socket_path} ({cadence})")

    host_tick_started = False
    reconciled = False
    try:
        server = build_early_shutdown_server(
            app,
            host=config.host,
            port=config.port,
            shutdown_signal=app.state.shutdown,
            listening_file=listening_file,
        )

        # Installed before `server.run()`'s own `capture_signals()` window opens.
        def _handle_signal(signum: int, frame: types.FrameType | None) -> None:
            server.handle_exit(signum, frame)

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        # Include halted runners: their active leases also bind the host's root.
        ids = [runner.runner_id for runner in host.runners]
        ids.extend(runner.id for runner in host.halted if runner.id is not None)
        _reconcile_host_scratch(config, host, ids)

        # Every runner's startup steps, before any driver or the host tick starts; a failing step fails the boot.
        for live in hosted.runners.live():
            try:
                run_startup_steps(live, hosted.start)
            except RunnerStartFailed as exc:
                raise exc.__cause__ or exc from None
        hosted.start.serving(drivers=drivers, scratch=own_scratch, said=_echo_note)

        # Report once before any runner ticks, so a runner the hub released under this host never claims across a
        # restart; a report that fails or times out starts the runners anyway.
        host.host_tick.report()
        reconciled = True
        for live in hosted.runners.live():
            if live.wired is not None and live.drivers is None:
                live.drivers = hosted.start.drive(live.runner, live.wired)
        if host_tick is not None:
            host_tick.start()
            host_tick_started = True
        server.run(sockets=sockets)
    finally:
        try:
            # Quiesce before marking; the spawner executors and engine outlive
            # the drain and are closed by the outer host frame.
            if host_tick is not None and host_tick_started:
                host_tick.stop()
            # A requested pass in flight is waited out, as a timed one is by its driver's join.
            for marked in hosted.runners.stop_all(mark=reconciled):
                click.echo(marked)
        finally:
            # uvicorn closes a pre-bound socket but does not unlink its file.
            Uds(config.socket_path).unlink()


def host_tick_driver(
    host: HostProcess, *, interval: Callable[[], float], stepper: HostPassStepper | None = None
) -> PeriodicPassDriver:
    """The host's own thread, passing on the tick's own interval with no jitter, so a credential lapsed across a
    restart is renewed at once; each slug's own cadence gates it within the pass. ``interval`` is the live tick
    interval it waits between passes, and ``stepper`` the host pass it
    drives, else one built over the host's tick."""
    return PeriodicPassDriver(
        (stepper or host_pass_stepper(host)).timed,
        name="blizzard-host-tick",
        label="host tick",
        log=get_logger("blizzard.runner.subscriptions"),
        interval=interval,
        jitter_seconds=0,
        signal=host.wakes.signal(),
        clock=host.monotonic_clock,
    )


def _pass_drivers(host: HostProcess, runner: RunnerProcess, stepper: RunnerStepper) -> list[PeriodicPassDriver]:
    """One runner's off-tick drivers."""
    drivers: list[PeriodicPassDriver] = []
    if runner.trace_sweep is not None:
        drivers.append(
            PeriodicPassDriver(
                stepper.timed_trace_sweep,
                name="blizzard-runner-trace-sweep",
                label="lease trace sweep",
                log=get_logger("blizzard.runner.trace_export"),
                interval=sweep_interval(host, stepper),
                signal=host.wakes.signal(),
                clock=host.monotonic_clock,
            )
        )
    return drivers


class ScratchLeaseRepository(IReadLeaseRecordRepository, ClosedLeaseLookup, Protocol):
    pass


def reconcile_scratch_roots(
    host_root: Path,
    root: Path,
    runners: Mapping[str, ScratchLeaseRepository],
    *,
    scratch_factory: ScratchSweeperFactory,
) -> None:
    """Bind and reconcile once, before any runner on this host can spawn.

    Each repository is already bound to its runner, including runners that could
    not be built. The caller holds the host lock, so reading the active leases
    before the root is prepared is safe.
    """
    leases = {
        runner_id: RunnerScratchLeases({lease.lease_id for lease in repo.list_active_leases()}, repo)
        for runner_id, repo in runners.items()
    }
    bind_scratch_root(
        ScratchBindingFiles(host_root / DATA_DIRNAME),
        root.resolve(),
        host_root / LEGACY_WORKER_SCRATCH_DIRNAME,
        leases,
        scratch_factory,
    )


def _echo_note(note: StartupNote) -> None:
    click.echo(note.message)


def _reconcile_host_scratch(
    config: RunnerConfig, host: HostProcess, runner_ids: list[str], *, every_named: bool = True
) -> None:
    """Reconcile the host's worker scratch for ``runner_ids`` — and, with ``every_named``, every runner the store's
    identity rows name."""
    errors = RunnerStoreErrorFactory(get_logger("blizzard.runner.store"))
    if every_named:
        runner_ids = list(dict.fromkeys((*runner_ids, *host.host_stores.runner_audit.named_runners())))
    repositories = {
        runner_id: build_read_stores(host.engine, errors=errors, runner_id=runner_id).lease_record
        for runner_id in runner_ids
    }
    root = config.worker_scratch_root or config.root / WORKER_SCRATCH_DIRNAME
    reconcile_scratch_roots(config.root, root, repositories, scratch_factory=WorkerScratchDirs)


@click.command("tick")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Host directory (overrides $BZ_RUNNER_DIR).",
)
@runner_option
@click.option(
    "--host",
    "host_pass",
    is_flag=True,
    help="Run the host-wide pass of a running host instead of a runner's; refused when no host is running.",
)
@click.pass_context
def tick_cmd(ctx: click.Context, directory: str, runner: str | None, host_pass: bool) -> None:
    """Run one pass of the host's runner against the live hub and workspace, then exit.

    While a host serves the directory, the pass is stepped on that host over its socket, by the runner --runner
    names or the host's only one; --host steps the host-wide pass instead. Exits 0 only when the pass completed.
    With no host serving, the pass runs here: refused on a store revision mismatch, like ``host``, while another
    process holds the directory, or while any of the host's runners is halted."""
    if host_pass and runner is not None:
        raise click.UsageError("--host and --runner are mutually exclusive: --host steps the host, not a runner")
    door = Door.of(ctx)
    if host_answers(HostLayout(Path(directory)).socket_path, door):
        _tick_running_host(directory, door, runner, host_pass)
        return
    if host_pass:
        raise click.ClickException(f"no host is serving {directory}, so there is no host pass to run")
    config = load_host(directory)
    with click_exception_on(ConfigError):
        entry = resolve_runner(config, runner, CliCollaborators.of(ctx).wall_clock)
    if entry.id is None:
        raise click.ClickException(f"runner {entry.name} is not built, so a tick cannot drive it")
    with store_write_guard(
        config,
        action="run a tick",
        door=Door.of(ctx),
        clock=CliCollaborators.of(ctx).wall_clock,
        refuse_foreign=True,
    ):
        seeded, seed_halted = seed_runners(config, clock=CliCollaborators.of(ctx).wall_clock)
        seed_halted += [item for item in config.projections() if isinstance(item, HaltedRunner) and item.id is None]
        if seed_halted:
            raise click.ClickException("\n".join(item.describe() for item in seed_halted))
        runner_built = next((item for item in seeded if item.id == entry.id), None)
        if runner_built is None:
            raise click.ClickException(f"runner {entry.name} ({entry.id}) is retired, so a tick cannot drive it")
        with click_exception_on(ConfigError):
            graph = InitCollaborators.of(ctx).build_runner_process(
                config, runner_built, publish_bundle=True, host_token=config.host_token.value
            )
        try:
            reader = RunnerSettingsReader(
                config,
                entry,
                graph.stores.settings,
                host_settings=HostSettingsReader(
                    config, graph.host.host_stores.settings, host_record_reads(graph.host.host_stores)
                ),
            )
            graph = replace(graph, settings_reader=reader, settings_seed=reader.seeder(graph.stores.settings))
            if snapshot := graph.harness_sets.current.bundle:
                click.echo(snapshot.summary())
            _reconcile_host_scratch(
                runner_built.config, graph.host, [entry.id for entry in config.runners if entry.id is not None]
            )
            LoopWiring.of(runner_built.config, bundle=graph.harness_sets.current.bundle).tick_once(graph)
        finally:
            graph.close()
    click.echo("tick complete")


def host_answers(sock: Path, door: Door) -> bool:
    """Whether a host answers its health probe on ``sock`` — a socket file with nothing listening, or anything that
    does not answer cleanly, is not one."""
    if not sock.exists():
        return False
    try:
        with door.uds_client(sock) as client:
            return client.get("/api/health").status_code == HTTPStatus.OK
    except (ConnectError, HTTPError):
        return False


def _tick_running_host(directory: str, door: Door, runner: str | None, host_pass: bool) -> None:
    """Step one pass on the host serving ``directory``: the host-wide one, or the selected runner's. Exits 1 on any
    outcome but ``completed``."""
    with RunnerDaemon.reach("tick", directory, None, door, runner) as daemon:
        response = daemon.send_to_host("post", "/api/host/ticks") if host_pass else daemon.send("post", "/api/ticks")
    if response.status_code == HTTPStatus.CONFLICT:
        raise click.ClickException("tick: a pass is already in progress on the host, so none was started")
    if response.status_code != HTTPStatus.OK:
        raise click.ClickException(f"tick: the host refused the pass ({response.status_code}): {response.text}")
    outcome = response.json()
    if outcome["outcome"] != SweepPassOutcome.COMPLETED:
        raise click.ClickException(f"tick {outcome['outcome']}: {outcome.get('error') or 'no reason reported'}")
    click.echo("tick complete")


@contextmanager
def read_stores(config: RunnerConfig) -> Generator[RunnerReadStores, None, None]:
    """The runner's read-only store bundle for a short-lived CLI verb, bound to the runner its host file configures —
    builds its own engine and disposes it on exit, so a caller never holds the engine past the bundle's use."""
    engine = create_engine_from_url(config.db_url)
    try:
        yield build_read_stores(
            engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), runner_id=config.runner_id
        )
    finally:
        engine.dispose()


@contextmanager
def read_host_stores(config: RunnerConfig | HostConfig) -> Generator[HostReadStores, None, None]:
    """:func:`read_stores`'s host-scoped twin, for a short-lived CLI verb reading a host table."""
    engine = create_engine_from_url(config.db_url)
    try:
        yield build_host_read_stores(engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))
    finally:
        engine.dispose()

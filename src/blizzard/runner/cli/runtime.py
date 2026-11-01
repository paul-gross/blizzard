"""``blizzard runner init``/``migrate``/``host``/``tick`` — scaffold the runtime and join its hub,
migrate, and drive the runtime."""

from __future__ import annotations

import os
import signal
import types
from collections.abc import Callable, Generator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from pathlib import Path

import click

from blizzard.cli.collaborators import CliCollaborators, ClientFactory, ConnectError, HTTPError
from blizzard.cli.config_inputs import packaged_flag_registry, strict_config
from blizzard.cli.host_directory import HostDirectory
from blizzard.cli.runtime import build_early_shutdown_server, click_exception_on, run_init, run_migrate
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.operator_sessions.internal.session_file import SessionFile
from blizzard.foundation.roles import collaborator, dto
from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE, hub_token_variable
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import (
    MigrationConnectionError,
    MigrationRunner,
    RevisionMismatchError,
    StoreBelowFloorError,
)
from blizzard.foundation.sweep_passes import SweepPassOutcome
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.app import HostedApp, build_hosted_app
from blizzard.runner.cli.daemon import Door, RunnerDaemon, daemon_holding, runner_option
from blizzard.runner.cli.directory import load_host
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.cli.store import runtime_of
from blizzard.runner.cli.store_guard import named_runners, store_write_guard
from blizzard.runner.composition import (
    HostProcess,
    RunnerProcess,
    build_host_process,
    build_host_read_stores,
    build_identity_forgetter,
    build_identity_writer,
    build_read_stores,
    build_runner_process,
    runner_store_migrations,
)
from blizzard.runner.config import DEFAULT_RUNNER_NAME, RunnerConfig, worker_shell_refusal
from blizzard.runner.config_table import ConfigError
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.bundle import BundleSnapshot
from blizzard.runner.harness.wiring import publish_harness_bundle
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
from blizzard.runner.host_layout import HOST_CONFIG_FILENAME, HostLayout
from blizzard.runner.hub.bootstrap import (
    BootstrapStop,
    BootstrapStopped,
    HeldToken,
    IRunnerStoreAside,
    JoinedRunner,
    RunnerBootstrap,
    StoreShared,
    TokenNotWritten,
    refuse_tokenless,
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
from blizzard.runner.hub.identity import RunnerIdentity, judged
from blizzard.runner.hub.internal.http_hub import HttpHostHubClient, HttpHubClient, HttpHubRunnerAdmin
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.lifecycle.identity_check import (
    ForeignStoreKind,
    foreign_store,
    foreign_store_reason,
    reset_hazard,
)
from blizzard.runner.listeners import ListenerError, Uds
from blizzard.runner.loop.mode import LoopMode
from blizzard.runner.loop.periodic_pass_driver import PeriodicPassDriver
from blizzard.runner.loop.step_passes import HostPassStepper
from blizzard.runner.loop_wiring import LoopWiring, PeriodicDriver, RunnerStepper, host_pass_stepper
from blizzard.runner.runtime import (
    StoreResetRefused,
    ensure_current_revision,
    migration_runner,
    named_store_socket,
    reset_command,
)
from blizzard.runner.store import MIGRATIONS_DIR
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.stores import HostReadStores, RunnerReadStores
from blizzard.runner.tracing.sweep import announce_rejected_tracing

ENV_TICK_SECONDS = "BZ_RUNNER_TICK_SECONDS"
DEFAULT_TICK_SECONDS = 30.0


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


@collaborator
@dataclass(frozen=True)
class InitCollaborators:
    """The hub clients, store migrations, host-process builder, and harness-bundle publisher a runtime verb
    acts through: an invoker hands its own to the root group as ``obj``; with none, the HTTP clients and the
    real builders are used."""

    hub_clients: Callable[[str, str, str | None], AbstractContextManager[InitHubClients]]
    host_clients: Callable[[str], AbstractContextManager[HostClients]]
    #: The runner store's migrations at a URL.
    store_migrations: Callable[[str], MigrationRunner] = runner_store_migrations
    build_host_process: Callable[..., HostProcess] = build_host_process
    publish_harness_bundle: Callable[..., BundleSnapshot] = publish_harness_bundle

    @classmethod
    def of(cls, ctx: click.Context) -> InitCollaborators:
        """The invoker's collaborators, or the HTTP ones."""
        return ctx.find_object(InitCollaborators) or cls.http(CliCollaborators.of(ctx).client_factory)

    @classmethod
    def http(cls, client_factory: ClientFactory) -> InitCollaborators:
        return cls(partial(_http_hub_clients, client_factory), partial(_http_host_clients, client_factory))

    def join_with(self, hub_url: str, token: str) -> AbstractContextManager[InitHubClients]:
        """The identity read and the add for ``hub_url``, under the held ``token`` and the operator's session."""
        return self.hub_clients(hub_url, token, SessionFile.of().load(hub_url))

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
    initialized: list[HostConfig] = []

    def initialize(root: Path) -> HostConfig:
        runtime = runtime_of(root, door, hub_url=hub_url, projects=named)
        if allow_readd and (aside := runtime.set_aside_pre_host_store(now=SystemClock().now())) is not None:
            for path in aside.moved:
                click.echo(f"set the pre-host store aside as {path}")
        initialized.append(runtime.init())
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
    store = _RuntimeStore(config, door)
    for entry in config.runners:
        _refuse_tokenless(config, entry, store, rerun)
    with collaborators.host_clients(config.hub_url) as host_clients:
        host = register_host(config, collaborators, host_clients, store, rerun)
        for entry in config.runners:
            _join_runner(config, entry, collaborators, HostJoin(host, host_clients(host.token)), store, rerun)


def write_runner_token(root: Path, token_env: str, token: str) -> None:
    """Write ``token`` to the host directory's ``.env`` as ``token_env``, for the verbs outside this composition
    root."""
    HubTokenFile.of(root, token_env).write(token)


def remove_runner_token(root: Path, token_env: str) -> None:
    """Remove ``token_env``'s assignment from the host directory's ``.env``, every other line kept — the move of a
    runner's token out of a host it leaves."""
    HubTokenFile.of(root, token_env).remove()


@dataclass(frozen=True)
class HostJoin:
    """The host init registered, and its own hub calls under the token it holds."""

    joined: JoinedHost
    client: IHostRunnerAdder


def register_host(
    config: HostConfig,
    collaborators: InitCollaborators,
    host_clients: HostClients,
    store: IRunnerStoreAside,
    rerun: _Rerun,
) -> JoinedHost:
    """Keep the host the held host token names, or add one named after the first ``[[runner]]`` and write its
    token; any stop exits non-zero with no host added."""
    held_token = config.host_token
    held = HeldToken(held_token.value, from_process_env=not held_token.rereads_dotenv()) if held_token.value else None
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
        if held_token.rereads_dotenv():
            _restrict(token_file)
    return host


def _adoptable(config: HostConfig, collaborators: InitCollaborators, rerun: _Rerun) -> tuple[str, ...]:
    """The runners a new host adopts as it is added: each whose held token the hub knows, in the tenant named if
    one is. A token the hub refuses, or a read that fails, adopts nothing — the runner's own join reports it."""
    adopt: list[str] = []
    for entry in config.runners:
        token = config.token_for(entry)
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
    token = config.token_for(entry)
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
) -> None:
    """Join ``entry``'s runner to the hub under the host, then write its id into the host file and its identity
    row into the store."""
    token = config.token_for(entry)
    with collaborators.join_with(config.hub_url, token.value) as (identity, _admin):
        joined = _join_hub(config, entry, token, identity, HostRunnerAdmin(host.client), store.for_runner(entry), rerun)
    if not joined.added and not host.joined.holds(joined.runner_id):
        raise click.ClickException(_stands_elsewhere(config, entry, joined, host.joined, rerun))
    # The token is written before the id, and the id before the identity row: a crash between any two is completed
    # by running init again, which keeps the runner the token names and, under --allow-readd, writes the id it
    # left stale. A configured id differing from the joined runner's reaches here only by a re-add.
    if entry.id != joined.runner_id:
        with click_exception_on(ConfigError):
            write_runner_id(config.layout, entry.name, joined.runner_id, strict=config.strict)
    _record_identity(config, joined)


def _stands_elsewhere(
    config: HostConfig, entry: RunnerEntry, joined: JoinedRunner, host: JoinedHost, rerun: _Rerun
) -> str:
    """The stop for a kept runner the hub does not hold under this host."""
    hub, host_id = config.hub_url, host.record.host_id
    return (
        f"runner {entry.name}'s token belongs to runner {joined.runner_id}, which the hub at {hub} does not hold "
        f"under this host ({host_id}), so its id and identity row were not written: place it under this host with "
        f"`blizzard hub runner-host adopt {host_id} {joined.runner_id} --hub-url {hub}`, then re-run "
        f"`{rerun.again()}`"
    )


def _refuse_unsettled(config: HostConfig, entry: RunnerEntry, joined: JoinedRunner) -> None:
    """Stop a kept runner whose store holds a runner neither it nor a sibling ``[[runner]]`` is, before anything is
    written. The configured id is not counted as ours: a store naming a stale one under a re-add is foreign too."""
    siblings = tuple(other.id for other in config.runners if other.name != entry.name and other.id is not None)
    verdict = foreign_store(named_runners(config), (joined.runner_id, *siblings))
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

    def __init__(self, config: HostConfig, door: Door) -> None:
        self.config = config
        self.door = door
        self._set_aside = False

    def stored_runner_id(self, *, sibling_of: RunnerEntry | None = None) -> str | None:
        """The runner the store's identity rows name, other than any another ``[[runner]]`` of the host configures
        beside ``sibling_of`` — asked of the cross-runner audit, since no runner is bound to a view yet."""
        siblings = {e.id for e in self.config.runners if sibling_of is not None and e.name != sibling_of.name}
        named = [runner_id for runner_id in named_runners(self.config) if runner_id not in siblings]
        return named[0] if named else None

    def is_set_aside(self) -> bool:
        return self._set_aside

    def set_aside(self) -> None:
        if self._set_aside:
            return
        aside = runtime_of(self.config.root, self.door).reset_store(now=SystemClock().now())
        self._set_aside = True
        for path in aside.moved:
            click.echo(f"set the old store aside as {path}")

    def for_runner(self, entry: RunnerEntry) -> IRunnerStoreAside:
        """The store as ``entry``'s runner joins it: the runners its siblings configure are theirs, not a former
        owner's."""
        return _RunnerStoreView(self, entry)


@dataclass(frozen=True)
class _RunnerStoreView:
    store: _RuntimeStore
    entry: RunnerEntry

    def stored_runner_id(self) -> str | None:
        return self.store.stored_runner_id(sibling_of=self.entry)

    def set_aside(self) -> None:
        """Set the shared store aside for this runner's re-add — a no-op once the host's own re-add set it aside,
        and refused while another ``[[runner]]`` stays beside it: setting the store aside would drop that runner's
        backlog and rewind its sequences below the hub's high-water marks."""
        if self.store.is_set_aside():
            return
        siblings = tuple(other.name for other in self.store.config.runners if other.name != self.entry.name)
        if siblings:
            raise StoreShared(siblings)
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


def _record_identity(config: HostConfig, joined: JoinedRunner) -> None:
    """Write the joined runner's identity row from the hub's answer, so a configured runner has its row before it
    writes anything else."""
    engine = create_engine_from_url(config.db_url)
    try:
        identities = build_identity_writer(
            engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), runner_id=joined.runner_id
        )
        identities.record_runner_identity(
            RunnerIdentity(
                runner_id=joined.runner_id,
                runner_name=joined.runner_name,
                registered_at=SystemClock().now(),
                tenant_id=joined.tenant_id,
                tenant_name=joined.tenant_name,
            )
        )
    finally:
        engine.dispose()


def forget_identity(config: HostConfig, runner_id: str) -> None:
    """Delete a retired runner's identity row from the host's store, so the store names only runners the host
    configures and a later start or ``init`` does not take it for another host's."""
    engine = create_engine_from_url(config.db_url)
    try:
        build_identity_forgetter(
            engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), runner_id=runner_id
        ).forget_runner_identity()
    finally:
        engine.dispose()


def _join_hub(
    config: HostConfig,
    entry: RunnerEntry,
    token: ResolvedToken,
    identity: ITokenIdentityReader,
    admin: IRunnerAdder,
    store: IRunnerStoreAside,
    rerun: _Rerun,
) -> JoinedRunner:
    """Keep the runner the held token names, or add one and write its token; any stop exits
    non-zero with nothing added."""
    joining, token_file = _joining_of(config, entry, token)
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
        _refuse_unsettled(config, entry, joined)
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
    return HeldToken(token.value, from_process_env=not token.rereads_dotenv()) if token.value else None


def _joining_of(config: HostConfig, entry: RunnerEntry, token: ResolvedToken) -> tuple[_Joining, HubTokenFile]:
    """What ``init`` joins for ``entry``, and the ``.env`` file its token is written to."""
    token_env = hub_token_variable(entry.name)
    joining = _Joining(
        root=config.root,
        hub_url=config.hub_url,
        name=entry.name,
        token_env=token_env,
        overridden_by=None if token.rereads_dotenv() else _source_words(token, token_env),
    )
    return joining, HubTokenFile.of(config.root, token_env)


def _source_words(token: ResolvedToken, token_env: str) -> str:
    """Where a token that overrides ``.env`` came from."""
    if token.source is TokenSource.HOST_FILE:
        return f"[hub_tokens] in {HOST_CONFIG_FILENAME}"
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
        else f"put it in {token_file.path} as {token_env}"
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
@click.pass_context
def host(
    ctx: click.Context,
    directory: str | None,
    dir_option: str,
    host_: str | None,
    port: int | None,
    loop: str | None,
    check: bool,
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
    config = load_host(directory, host=host_, port=port, loop=loop_mode)
    for warning in config.warnings:
        click.echo(f"warning: {warning}")
    projections = config.projections()
    runners = [built for built in projections if isinstance(built, BuiltRunner)]
    halted = [runner for runner in projections if isinstance(runner, HaltedRunner)]
    ours = tuple(entry.id for entry in config.runners if entry.id is not None)
    with store_write_guard(
        config,
        action="start the host",
        door=Door.of(ctx),
        runners={built.id: built.config.name for built in runners},
        configured=ours,
        refuse_foreign=False,
    ) as foreign:
        kept = [built for built in runners if built.id not in foreign]
        halted += [
            HaltedRunner(name=built.config.name, id=built.id, errors=(foreign[built.id],))
            for built in runners
            if built.id in foreign
        ]
        start(config, kept, halted, InitCollaborators.of(ctx))


def start(
    config: HostConfig, runners: Sequence[BuiltRunner], halted: Sequence[HaltedRunner], collaborators: InitCollaborators
) -> None:
    """Start the host over its built runners, reporting each halted one; returns when the host has stopped."""
    for runner in halted:
        click.echo(f"warning: {runner.describe()}")
    for built in runners:
        if refusal := worker_shell_refusal(built.config.worker_shell):
            raise click.ClickException(f"runner {built.config.name}: {refusal}")
    bundles = {built.id: publish_runner_bundle(built.config, collaborators) for built in runners}
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
    process = collaborators.build_host_process(
        config, runners, halted=halted, events=broker, bundles=bundles, trace_platform=True
    )
    try:
        shared = runners[0].config if runners else config.host_view()
        # A configured-but-missing prompt file raises while the steppers are built, before any socket binds.
        with click_exception_on(ConfigError):
            hosted = build_hosted_app(shared, process)
        try:
            _serve_host(shared, process, hosted, loop=config.loop)
        finally:
            hosted.close()
    finally:
        process.close()


def _check(ctx: click.Context, root: Path, *, host: str | None, port: int | None, loop: LoopMode | None) -> None:
    """``host --check``: every error a start would meet, reported together; starts nothing and holds nothing."""
    try:
        config = HostConfig.load_for_store(
            root, host=host, port=port, loop=loop, flag_registry=packaged_flag_registry(), strict=strict_config()
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
        named = named_runners(config)
    except (RevisionMismatchError, MigrationConnectionError, StoreBelowFloorError) as exc:
        problems.append(f"store: {exc}")
        named = ()
    collaborators = InitCollaborators.of(ctx)
    for projection in config.projections():
        if isinstance(projection, HaltedRunner):
            problems.append(projection.describe())
            continue
        problem = foreign_store_reason(
            named,
            projection.id,
            reset_command(config.root),
            configured=tuple(entry.id for entry in config.runners if entry.id is not None),
        ) or _resolve_token(collaborators, config.hub_url, projection)
        if problem is None:
            click.echo(
                f"runner {projection.config.name} ({projection.id}): its token ({projection.token.source}) "
                "resolves to its id"
            )
        else:
            problems.append(f"runner {projection.config.name} ({projection.id}): {problem}")
    if problems:
        raise click.ClickException("\n".join(problems))
    click.echo("host ok")


def _resolve_token(collaborators: InitCollaborators, hub_url: str, built: BuiltRunner) -> str | None:
    """Why ``built``'s token does not resolve to its id at the hub, or ``None`` when it does."""
    try:
        with collaborators.identity_for(hub_url, built.token.value) as (identity, _admin):
            answer = identity.identity()
    except HubClientError as exc:
        return f"cannot resolve its token at the hub {hub_url}: {exc}"
    if isinstance(answer, TokenRefusal):
        return f"the hub at {hub_url} refuses its token ({answer.reason})"
    if judged(built.id, answer.runner_id) is IdentityState.MISMATCHED:
        return f"its token resolves to runner {answer.runner_id}, not the configured {built.id}"
    return None


def publish_runner_bundle(config: RunnerConfig, collaborators: InitCollaborators) -> BundleSnapshot | None:
    """Load the operator's harness-config bundle and publish its snapshot before any work is
    accepted, returning it for the process graph to deliver; a runtime with no
    `[harness] config_dir` is left untouched."""
    if config.harness_config_dir is None:
        return None
    with click_exception_on(ConfigError):
        snapshot = collaborators.publish_harness_bundle(
            config.harness_config_dir,
            config.root,
            autonomy=config.autonomy,
            sections=config.harness_sections,
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


def _runner_threads(runner: RunnerProcess, stepper: RunnerStepper, *, interval: float) -> _RunnerThreads:
    driver = PeriodicDriver(runner.config, interval_seconds=interval, process_graph=runner, stepper=stepper)
    return _RunnerThreads(runner, driver, _pass_drivers(runner, stepper))


def _serve_host(config: RunnerConfig, host: HostProcess, hosted: HostedApp, *, loop: LoopMode) -> None:
    """Serve ``hosted`` until stopped. A ``timed`` loop starts every pass on its timer; a ``stepped`` one starts
    none, and a pass runs only when requested through its stepper."""
    app = hosted.app
    interval = float(os.environ.get(ENV_TICK_SECONDS, DEFAULT_TICK_SECONDS))
    for runner in host.runners:
        announce_rejected_tracing(runner.trace_settings, runner.stores.outbound, runner.clock.now())
    stepped = loop is LoopMode.STEPPED
    threads = (
        []
        if stepped
        else [
            _runner_threads(runner, stepper, interval=interval)
            for runner, stepper in zip(host.runners, hosted.steppers, strict=True)
        ]
    )
    host_tick = (
        None
        if stepped or hosted.host_stepper is None
        else host_tick_driver(host, tick_seconds=interval, stepper=hosted.host_stepper)
    )

    # Two doors onto the one app, bound up front so a clash fails startup loudly and
    # served by the single `Server` below, which keeps the shutdown path on one frame.
    with click_exception_on(ListenerError):
        sockets = host.listeners.bound()
    cadence = "stepped: a pass runs only on request" if stepped else f"loop tick {interval}s"
    click.echo(f"serving blizzard-runner on {config.host}:{config.port} and {config.socket_path} ({cadence})")

    started: list[_RunnerThreads] = []
    host_tick_started = False
    reconciled = False
    try:
        server = build_early_shutdown_server(
            app, host=config.host, port=config.port, shutdown_signal=app.state.shutdown
        )

        # Installed before `server.run()`'s own `capture_signals()` window opens.
        def _handle_signal(signum: int, frame: types.FrameType | None) -> None:
            server.handle_exit(signum, frame)

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        # Mark crash-orphaned sessions, for every runner, before any driver or the host tick starts.
        for resume in hosted.resumes:
            resumable = resume.on_startup()
            if resumable:
                click.echo(f"marked {resumable} crash-interrupted lease(s) for restart-resume")

        # Report once before any runner ticks, so a runner the hub released under this host never claims across a
        # restart; a report that fails or times out starts the runners anyway.
        host.host_tick.report()
        reconciled = True
        for runner_threads in threads:
            runner_threads.start()
            started.append(runner_threads)
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
            for runner_threads in reversed(started):
                runner_threads.stop()
            # A requested pass in flight is waited out, as a timed one is by its driver's join.
            for stepper in hosted.steppers:
                stepper.quiesce()
            if reconciled:
                for resume in hosted.resumes:
                    marked = resume.on_shutdown()
                    if marked:
                        click.echo(f"marked {marked} in-flight lease(s) for restart-resume")
        finally:
            # uvicorn closes a pre-bound socket but does not unlink its file.
            Uds(config.socket_path).unlink()


def host_tick_driver(
    host: HostProcess, *, tick_seconds: float, stepper: HostPassStepper | None = None
) -> PeriodicPassDriver:
    """The host's own thread, passing on the tick's own interval with no jitter, so a credential lapsed across a
    restart is renewed at once; each slug's own cadence gates it within the pass. ``stepper`` is the host pass it
    drives, else one built over the host's tick."""
    return PeriodicPassDriver(
        (stepper or host_pass_stepper(host)).timed,
        name="blizzard-host-tick",
        label="host tick",
        log=get_logger("blizzard.runner.subscriptions"),
        interval_seconds=tick_seconds,
        jitter_seconds=0,
    )


def _pass_drivers(runner: RunnerProcess, stepper: RunnerStepper) -> list[PeriodicPassDriver]:
    """One runner's off-tick drivers."""
    drivers: list[PeriodicPassDriver] = []
    if runner.trace_sweep is not None:
        drivers.append(
            PeriodicPassDriver(
                stepper.timed_trace_sweep,
                name="blizzard-runner-trace-sweep",
                label="lease trace sweep",
                log=get_logger("blizzard.runner.trace_export"),
                interval_seconds=runner.config.tracing.sweep_seconds,
            )
        )
    return drivers


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
    if _host_answers(HostLayout(Path(directory)).socket_path, door):
        _tick_running_host(directory, door, runner, host_pass)
        return
    if host_pass:
        raise click.ClickException(f"no host is serving {directory}, so there is no host pass to run")
    config = load_host(directory)
    built = [projection for projection in config.projections() if isinstance(projection, BuiltRunner)]
    halted = [projection for projection in config.projections() if isinstance(projection, HaltedRunner)]
    if halted:
        raise click.ClickException("\n".join(runner.describe() for runner in halted))
    with click_exception_on(ConfigError):
        entry = config.sole_runner(runner)
    picked = [projection for projection in built if projection.id == entry.id]
    if not picked:
        raise click.ClickException(f"runner {entry.name} is not built, so a tick cannot drive it")
    (runner_built,) = picked
    with store_write_guard(
        config,
        action="run a tick",
        door=Door.of(ctx),
        runners={runner_built.id: runner_built.config.name},
        configured=tuple(entry.id for entry in config.runners if entry.id is not None),
        refuse_foreign=True,
    ):
        bundle = publish_runner_bundle(runner_built.config, InitCollaborators.of(ctx))
        graph = build_runner_process(
            runner_built.config,
            bundle=bundle,
            runner_id=runner_built.id,
            token=runner_built.token,
            host_token=config.host_token.value,
        )
        try:
            LoopWiring.of(runner_built.config, bundle=bundle).tick_once(process=graph)
        finally:
            graph.close()
    click.echo("tick complete")


def _host_answers(sock: Path, door: Door) -> bool:
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
def read_host_stores(config: RunnerConfig) -> Generator[HostReadStores, None, None]:
    """:func:`read_stores`'s host-scoped twin, for a short-lived CLI verb reading a host table."""
    engine = create_engine_from_url(config.db_url)
    try:
        yield build_host_read_stores(engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))
    finally:
        engine.dispose()

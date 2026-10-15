"""``blizzard runner init``/``migrate``/``host``/``tick`` — scaffold the runtime and join its hub,
migrate, and drive the runtime."""

from __future__ import annotations

import os
import signal
import types
from collections.abc import Callable, Iterator, Sequence
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
from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationConnectionError, RevisionMismatchError
from blizzard.runner.app import HostedApp, build_hosted_app
from blizzard.runner.cli.daemon import daemon_holding, runner_option
from blizzard.runner.cli.directory import load_host
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.cli.store import runtime_of
from blizzard.runner.composition import (
    HostProcess,
    RunnerProcess,
    build_host_process,
    build_host_read_stores,
    build_host_stores,
    build_identity_writer,
    build_read_stores,
    build_runner_process,
)
from blizzard.runner.config import RunnerConfig
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
from blizzard.runner.host_layout import HOST_CONFIG_FILENAME, HostHeld, HostLock, write_runner_id
from blizzard.runner.hub.bootstrap import (
    BootstrapStop,
    BootstrapStopped,
    HeldToken,
    IRunnerStoreAside,
    JoinedRunner,
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
    TokenRefusal,
)
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.hub.internal.http_hub import HttpHubClient, HttpHubRunnerAdmin
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.lifecycle.identity_check import foreign_store_reason
from blizzard.runner.listeners import ListenerError, Uds
from blizzard.runner.loop_wiring import LoopWiring, PeriodicDriver
from blizzard.runner.runtime import (
    IStoreLocation,
    StoreResetRefused,
    ensure_current_revision,
    migration_runner,
    named_store_socket,
    reset_command,
)
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.stores import HostReadStores, RunnerReadStores
from blizzard.runner.tracing.sweep import announce_rejected_tracing
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

    def join_with(self, hub_url: str, token: str) -> AbstractContextManager[InitHubClients]:
        """The identity read and the add for ``hub_url``, under the held ``token`` and the operator's session."""
        return self.hub_clients(hub_url, token, SessionFile.of().load(hub_url))

    def identity_for(self, hub_url: str, token: str) -> AbstractContextManager[InitHubClients]:
        """The identity read alone is wanted of the pair: ``--check`` resolves a runner's token with no session."""
        return self.hub_clients(hub_url, token, None)


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
    """Scaffold the host file + data dir + a migrated store under DIRECTORY, then join the host's runner to
    its hub. Idempotent.

    A runner holding no token is added at the hub — under your `blizzard hub login` session when you
    hold one, in the tenant --tenant names or else the only one you may enter — its token written to
    DIRECTORY/.env as BZ_HUB_TOKEN_<NAME> and its id to the host file's `[[runner]]`. A held token whose runner
    serves another tenant than --tenant names stops: a runner never moves between tenants. A fresh host serves
    the projects --project names, else `default`; the runner is added serving them, or, with none named, the
    tenant's only project. A directory a pre-host runner left behind is refused, naming graduation, unless
    --allow-readd sets its store aside. DIRECTORY defaults to $BZ_RUNNER_DIR, then the cwd."""
    named = projects or None
    trace = OperatorTrace.source(ctx)
    initialized: list[HostConfig] = []

    def initialize(root: Path) -> HostConfig:
        runtime = runtime_of(root, trace, hub_url=hub_url, projects=named)
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
    entry = config.sole_runner()
    token = config.token_for(entry)
    collaborators = ctx.find_object(InitCollaborators) or InitCollaborators(_http_hub_clients)
    rerun = _Rerun(config.root, tenant_id=tenant_id, allow_readd=allow_readd, projects=named)
    if (
        entry.id is not None
        and not token.value
        and not allow_readd
        and _RuntimeStore(config, trace).stored_runner_id() is None
    ):
        raise click.ClickException(
            f"runner {entry.name} is configured as {entry.id} but holds no token, so init would add another runner "
            f"in its place; restore its token, or re-run `{rerun.readding()}` to set its store aside and add a new one"
        )
    with collaborators.join_with(config.hub_url, token.value) as (identity, admin):
        if entry.id is not None and token.value and not allow_readd:
            _refuse_other_runner(entry, identity)
        joined = _join_hub(config, entry, token, identity, admin, _RuntimeStore(config, trace), rerun)
    # The token is written before the id, and the id before the identity row: a crash between any two is completed
    # by running init again, which keeps the runner the token names and, under --allow-readd, writes the id it
    # left stale. A configured id differing from the joined runner's reaches here only by a re-add.
    if entry.id != joined.runner_id:
        with click_exception_on(ConfigError):
            write_runner_id(config.layout, entry.name, joined.runner_id)
    _record_identity(config, joined)


def _refuse_other_runner(entry: RunnerEntry, identity: ITokenIdentityReader) -> None:
    """Stop when the held token belongs to a runner other than the one ``entry`` configures, before anything is
    written: the configured id is the runner's, and only a re-add replaces it. A hub that cannot answer is left to
    the join, which reports it."""
    try:
        answer = identity.identity()
    except HubClientError:
        return
    if isinstance(answer, TokenIdentity) and answer.runner_id != entry.id:
        raise click.ClickException(
            f"runner {entry.name} is configured as {entry.id}, but its token belongs to runner {answer.runner_id}; "
            f"restore the token of {entry.id} (or correct the `id` in the host file), or add a new runner in its "
            "place with --allow-readd. No token, host-file id or identity row was written"
        )


def _refuse_unsettled(config: HostConfig, entry: RunnerEntry, joined: JoinedRunner, rerun: _Rerun) -> None:
    """Stop a kept runner whose token does not settle into this directory, before anything is written: its store
    names another runner, or the host file configures another id and no re-add was asked for. A kept runner joins
    a store set aside for it or one naming only itself, and a configured id differing from its own is completed
    only under a re-add — the state a crash between the token and the id leaves."""
    others = [named for named in named_runners(config) if named != joined.runner_id]
    if others:
        raise click.ClickException(
            f"runner {entry.name}'s token belongs to runner {joined.runner_id}, but the store here names runner "
            f"{', '.join(others)}; restore the token of {others[0]}, or set the store aside with "
            f"`{reset_command(config.root)}` to start the token's runner on a fresh "
            "one. No token, host-file id or identity row was written"
        )
    if entry.id is not None and entry.id != joined.runner_id and not rerun.allow_readd:
        raise click.ClickException(
            f"runner {entry.name} is configured as {entry.id}, but its token belongs to runner {joined.runner_id}; "
            f"restore the token of {entry.id}, or re-run `{rerun.readding()}` to take the token's runner in its "
            "place. No token, host-file id or identity row was written"
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


def named_runners(config: IStoreLocation) -> tuple[str, ...]:
    """The ids of the runners the store's identity rows name, oldest first — read through the cross-runner audit,
    the one read exempt from a runner's scope."""
    engine = create_engine_from_url(config.db_url)
    try:
        return build_host_stores(
            engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), clock=SystemClock()
        ).runner_audit.named_runners()
    finally:
        engine.dispose()


def configured_ids(config: RunnerConfig) -> tuple[str, ...]:
    """The ids every ``[[runner]]`` of ``config``'s host file configures — the runners a store it opens may name."""
    try:
        host = HostConfig.load(config.root)
    except ConfigError:
        return ()
    return tuple(entry.id for entry in host.runners if entry.id is not None)


class _RuntimeStore:
    """The host's own store, as ``init`` sees it while the host is down."""

    def __init__(self, config: HostConfig, trace: ITraceHeaders) -> None:
        self.config = config
        self.trace = trace

    def stored_runner_id(self) -> str | None:
        """The runner the store's identity rows name — asked of the cross-runner audit, since no runner is
        configured yet to bind a view to."""
        named = named_runners(self.config)
        return named[0] if named else None

    def set_aside(self) -> None:
        with click_exception_on(ConfigError, StoreResetRefused):
            aside = runtime_of(self.config.root, self.trace).reset_store(now=SystemClock().now())
        for path in aside.moved:
            click.echo(f"set the old runner's store aside as {path}")


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


def _join_hub(
    config: HostConfig,
    entry: RunnerEntry,
    token: ResolvedToken,
    identity: ITokenIdentityReader,
    admin: IHubRunnerAdmin,
    store: IRunnerStoreAside,
    rerun: _Rerun,
) -> JoinedRunner:
    """Keep the runner the held token names, or add one and write its token; any stop exits
    non-zero with nothing added."""
    token_env = hub_token_variable(entry.name)
    token_file = HubTokenFile.of(config.root, token_env)
    held = HeldToken(token.value, from_process_env=not token.rereads_dotenv()) if token.value else None
    joining = _Joining(
        root=config.root,
        hub_url=config.hub_url,
        name=entry.name,
        token_env=token_env,
        overridden_by=None if token.rereads_dotenv() else _source_words(token, token_env),
    )
    hub = config.hub_url
    try:
        joined = RunnerBootstrap(identity, admin, token_file, store).join(
            entry.name, held=held, allow_readd=rerun.allow_readd, tenant_id=rerun.tenant_id, projects=rerun.projects
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
    if not joined.added:
        _refuse_unsettled(config, entry, joined, rerun)
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
    help="Host directory (overrides $BZ_RUNNER_DIR).",
)
@click.option("--down", default=None, help="Reverse migrations down to this revision (e.g. base).")
@click.pass_context
def migrate_cmd(ctx: click.Context, directory: str, down: str | None) -> None:
    """Apply pending store migrations, or reverse with --down <rev>.

    A directory that has not graduated to a host is refused and left as it is. A graduated one holding a pre-host
    `data/runner.db` and no `data/host.db` adopts that store first. Adoption and --down hold the directory and are
    refused while a host is running; a plain upgrade is refused only while a pre-host daemon answers."""
    trace = OperatorTrace.source(ctx)
    run_migrate(
        Path(directory),
        down,
        migrate=lambda root, down: runtime_of(root, trace).migrate(down=down),
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
    "--check",
    is_flag=True,
    help="Run every check a start would — the host file, the store's revision, each runner's token resolved to its "
    "id — report each error, and start nothing.",
)
@click.pass_context
def host(
    ctx: click.Context, directory: str | None, dir_option: str, host_: str | None, port: int | None, check: bool
) -> None:
    """Become the blizzard host: the reconciliation loop of each runner + the local API.

    DIRECTORY (positional) and --dir are equivalent — pass one; giving both requires
    they agree. Defaults to $BZ_RUNNER_DIR, then the cwd. A directory that has not graduated to a host is
    refused, naming the graduation procedure."""
    directory = HostDirectory(directory, dir_option).path
    if check:
        _check(ctx, Path(directory), host=host_, port=port)
        return
    config = load_host(directory, host=host_, port=port)
    for warning in config.warnings:
        click.echo(f"warning: {warning}")
    projections = config.projections()
    runners = [built for built in projections if isinstance(built, BuiltRunner)]
    halted = [runner for runner in projections if isinstance(runner, HaltedRunner)]
    layout = config.layout
    try:
        lock = HostLock.acquire(layout)
    except HostHeld as exc:
        raise click.ClickException(f"{exc}, so this host cannot start — stop it first") from exc
    with lock:
        holding = daemon_holding(layout.legacy_socket_path, OperatorTrace.source(ctx))
        if holding is not None:
            raise click.ClickException(f"{holding}, so this host cannot start — stop it first")
        with click_exception_on(RevisionMismatchError, MigrationConnectionError):
            ensure_current_revision(config)
        named = named_runners(config)
        ours = tuple(entry.id for entry in config.runners if entry.id is not None)
        kept: list[BuiltRunner] = []
        for built in runners:
            if (reason := foreign_store_reason(named, built.id, reset_command(config.root), configured=ours)) is None:
                kept.append(built)
            else:
                halted.append(HaltedRunner(name=built.config.name, id=built.id, errors=(reason,)))
        _start(config, kept, halted)


def _start(config: HostConfig, runners: Sequence[BuiltRunner], halted: Sequence[HaltedRunner]) -> None:
    """Start the host over its built runners, reporting each halted one; returns when the host has stopped."""
    for runner in halted:
        click.echo(f"warning: {runner.describe()}")
    bundles = {built.id: _publish_harness_bundle(built.config) for built in runners}
    for built in runners:
        for missing in built.config.missing_worker_path_prepend_entries:
            click.echo(f"warning: runner {built.config.name}: [worker] path_prepend entry does not exist: {missing}")
        if built.config.dropped_otel_passthrough:
            names = ", ".join(built.config.dropped_otel_passthrough)
            click.echo(
                f"warning: runner {built.config.name}: [worker] env_passthrough OTEL_* names are dropped while "
                f"tracing is on: {names}"
            )
    # One broker for the process: `host` is the one composer building both the
    # served app and the ticked loops, so every writer and the stream route share it.
    broker = EventBroker()
    process = build_host_process(config, runners, halted=halted, events=broker, bundles=bundles, trace_platform=True)
    try:
        shared = runners[0].config if runners else config.host_view()
        hosted = build_hosted_app(shared, process)
        try:
            _serve_host(shared, process, hosted)
        finally:
            hosted.close()
    finally:
        process.close()


def _check(ctx: click.Context, root: Path, *, host: str | None, port: int | None) -> None:
    """``host --check``: every error a start would meet, reported together; starts nothing and holds nothing."""
    try:
        config = HostConfig.load_for_store(root, host=host, port=port)
    except HostConfigError as exc:
        raise click.ClickException("\n".join(f"host: {error}" for error in exc.errors)) from exc
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    for warning in config.warnings:
        click.echo(f"warning: {warning}")
    problems: list[str] = []
    named_socket = named_store_socket(config)
    if named_socket is not None and (held := daemon_holding(named_socket, OperatorTrace.source(ctx))) is not None:
        problems.append(
            f"store: {held}, and it holds the store {config.db_url} the host file names; "
            "stop it, or drop the `db_url` so the host's own store applies"
        )
    try:
        ensure_current_revision(config)
        click.echo(f"store: {config.db_url} is at the revision this build expects")
        named = named_runners(config)
    except (RevisionMismatchError, MigrationConnectionError) as exc:
        problems.append(f"store: {exc}")
        named = ()
    collaborators = ctx.find_object(InitCollaborators) or InitCollaborators(_http_hub_clients)
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
    if answer.runner_id != built.id:
        return f"its token resolves to runner {answer.runner_id}, not the configured {built.id}"
    return None


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


@dataclass(frozen=True)
class _RunnerThreads:
    """One runner's serving threads: its reconciliation driver and the off-tick drivers beside it."""

    runner: RunnerProcess
    driver: PeriodicDriver
    pass_drivers: list[PeriodicPassDriver]

    def start(self) -> None:
        self.driver.start()  # startup recovery is REAP running first inside the tick
        for pass_driver in self.pass_drivers:
            pass_driver.start()

    def stop(self) -> None:
        for pass_driver in self.pass_drivers:
            pass_driver.stop()
        self.driver.stop()


def _runner_threads(runner: RunnerProcess, *, interval: float) -> _RunnerThreads:
    config = runner.config
    # `PeriodicDriver` resolves its prompt files on this thread, not in the loop thread: a
    # configured-but-missing prompt raises here, before any socket binds.
    with click_exception_on(ConfigError):
        driver = PeriodicDriver(config, interval_seconds=interval, process_graph=runner)
    announce_rejected_tracing(runner.trace_settings, runner.stores.outbound, runner.clock.now())
    return _RunnerThreads(runner, driver, _pass_drivers(config, runner))


def _serve_host(config: RunnerConfig, host: HostProcess, hosted: HostedApp) -> None:
    app = hosted.app
    interval = float(os.environ.get(ENV_TICK_SECONDS, DEFAULT_TICK_SECONDS))
    threads = [_runner_threads(runner, interval=interval) for runner in host.runners]
    host_tick = _host_tick_driver(host, tick_seconds=interval)

    # Two doors onto the one app, bound up front so a clash fails startup loudly and
    # served by the single `Server` below, which keeps the shutdown path on one frame.
    with click_exception_on(ListenerError):
        sockets = host.listeners.bound()
    click.echo(
        f"serving blizzard-runner on {config.host}:{config.port} and {config.socket_path} (loop tick {interval}s)"
    )

    started: list[_RunnerThreads] = []
    host_tick_started = False
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

        # Mark crash-orphaned sessions, for every runner, before any driver or the host tick starts.
        for resume in hosted.resumes:
            resumable = resume.on_startup()
            if resumable:
                click.echo(f"marked {resumable} crash-interrupted lease(s) for restart-resume")

        # Report once before any runner ticks, so a runner the hub released under this host never claims across a
        # restart; a report that fails or times out starts the runners anyway.
        host.host_tick.report()
        for runner_threads in threads:
            runner_threads.start()
            started.append(runner_threads)
        host_tick.start()
        host_tick_started = True
        server.run(sockets=sockets)
    finally:
        try:
            # Quiesce before marking; the spawner executors and engine outlive
            # the drain and are closed by the outer host frame.
            if host_tick_started:
                host_tick.stop()
            for runner_threads in reversed(started):
                runner_threads.stop()
            if started:
                for resume in hosted.resumes:
                    marked = resume.on_shutdown()
                    if marked:
                        click.echo(f"marked {marked} in-flight lease(s) for restart-resume")
        finally:
            # uvicorn closes a pre-bound socket but does not unlink its file.
            Uds(config.socket_path).unlink()


def _host_tick_driver(host: HostProcess, *, tick_seconds: float) -> PeriodicPassDriver:
    """The host's own thread, built only here so `runner tick` never runs a credential renewal. It passes on the
    tick's own interval with no jitter, so a credential lapsed across a restart is renewed at once; each slug's own
    cadence gates it within the pass."""
    return PeriodicPassDriver(
        host.host_tick.run,
        name="blizzard-host-tick",
        label="host tick",
        log=get_logger("blizzard.runner.subscriptions"),
        interval_seconds=tick_seconds,
        jitter_seconds=0,
    )


def _pass_drivers(config: RunnerConfig, runner: RunnerProcess) -> list[PeriodicPassDriver]:
    """One runner's off-tick drivers, built only here so `runner tick` never runs a trace sweep."""
    drivers: list[PeriodicPassDriver] = []
    if runner.trace_sweep is not None:
        drivers.append(
            PeriodicPassDriver(
                runner.trace_sweep.sweep,
                name="blizzard-runner-trace-sweep",
                label="lease trace sweep",
                log=get_logger("blizzard.runner.trace_export"),
                interval_seconds=config.tracing.sweep_seconds,
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
@click.pass_context
def tick_cmd(ctx: click.Context, directory: str, runner: str | None) -> None:
    """Run ONE synchronous reconciliation tick (REAP → PULL → FILL → ADVANCE) of the host's runner.

    The steppable-loop driver for tests and the e2e (``bzh:steppable-loop``): a single pass against the
    live hub and workspace, then exit. Refuses on a store revision mismatch, like ``host``, and while another
    process holds the directory."""
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
    try:
        lock = HostLock.acquire(config.layout)
    except HostHeld as exc:
        raise click.ClickException(f"{exc}, so this tick cannot run — stop it first") from exc
    with lock:
        holding = daemon_holding(config.layout.legacy_socket_path, OperatorTrace.source(ctx))
        if holding is not None:
            raise click.ClickException(f"{holding}, so this tick cannot run — stop it first")
        with click_exception_on(RevisionMismatchError, MigrationConnectionError):
            ensure_current_revision(config)
        if (
            reason := foreign_store_reason(
                named_runners(config),
                runner_built.id,
                reset_command(config.root),
                configured=tuple(entry.id for entry in config.runners if entry.id is not None),
            )
        ) is not None:
            raise click.ClickException(f"runner {runner_built.config.name} ({runner_built.id}): {reason}")
        bundle = _publish_harness_bundle(runner_built.config)
        graph = build_runner_process(
            runner_built.config, bundle=bundle, runner_id=runner_built.id, token=runner_built.token
        )
        try:
            LoopWiring.of(runner_built.config, bundle=bundle).tick_once(process=graph)
        finally:
            graph.close()
    click.echo("tick complete")


@contextmanager
def read_stores(config: RunnerConfig) -> Iterator[RunnerReadStores]:
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
def read_host_stores(config: RunnerConfig) -> Iterator[HostReadStores]:
    """:func:`read_stores`'s host-scoped twin, for a short-lived CLI verb reading a host table."""
    engine = create_engine_from_url(config.db_url)
    try:
        yield build_host_read_stores(engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))
    finally:
        engine.dispose()

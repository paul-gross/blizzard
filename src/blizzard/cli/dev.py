"""``blizzard dev <cmd>`` — a hidden developer/operator surface.

Not part of the product CLI (``hub`` / ``runner``): these verbs inspect a store's raw
facts for debugging and crash-recovery verification, so the group is ``hidden=True``.
"""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path

import click
from click.core import ParameterSource

from blizzard.cli.collaborators import CliCollaborators, ClientFactory, HTTPError
from blizzard.cli.config_inputs import packaged_flag_registry, strict_config
from blizzard.cli.operator_trace import OperatorTrace
from blizzard.cli.param_rank import ParamSource
from blizzard.foundation.invariants import Violation
from blizzard.foundation.operator_sessions import IReadSessionStore
from blizzard.foundation.operator_sessions.internal.session_file import SessionFile
from blizzard.hub.cli.collaborators import HubCliCollaborators
from blizzard.hub.cli.context import ENV_HUB_URL, CliContext
from blizzard.hub.cli.session_service import SessionService
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.runner.cli.control import ENV_LOCAL_API_URL
from blizzard.runner.cli.env import ENV_RUNNER_DIR
from blizzard.runner.config_table import ConfigError as RunnerConfigError
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_layout import HostLayout
from blizzard.tools.invariants import Invariants
from blizzard.wire.invariants import InvariantsResponse

# The hub's fact checks read every chunk, so a client waits longer than the verbs' fifteen seconds.
CHECK_TIMEOUT = 60.0
_HUB_PATH = "/api/admin/invariants"
_HOST_PATH = "/api/host/invariants"
_RUNNING = "another invariant check is running — try again when it finishes"


@click.group(hidden=True)
@click.pass_context
def dev(ctx: click.Context) -> None:
    """Developer tools for checking a hub's or runner's stores."""
    # The session service the `hub` group wires, read-only here: the client mode only loads a stored session.
    HubCliCollaborators.resolve(ctx, lambda: SessionService(SessionFile.of()))


@dev.command("check-invariants")
@click.option("--runner-dir", "runner_dir", default=None, help="Offline: host directory whose runner store to check.")
@click.option("--hub-dir", "hub_dir", default=None, help="Offline: hub runtime directory to check.")
@click.option(
    "--allow-external-db",
    "allow_external_db",
    is_flag=True,
    default=False,
    help="Offline: proceed even if --hub-dir's config names a database outside that directory.",
)
@click.option(
    "--hub-url",
    "hub_url",
    default=None,
    envvar=ENV_HUB_URL,
    help=f"Client: ask the running hub at this URL, as the `blizzard hub login` session (overrides ${ENV_HUB_URL}).",
)
@click.option(
    "--dir",
    "directory",
    default=None,
    envvar=ENV_RUNNER_DIR,
    help=f"Client: ask the running host whose socket this directory holds (overrides ${ENV_RUNNER_DIR}).",
)
@click.option(
    "--runner-url",
    "runner_url",
    default=None,
    envvar=ENV_LOCAL_API_URL,
    help=f"Client: ask the running host's local API over TCP (overrides ${ENV_LOCAL_API_URL}).",
)
@click.pass_context
def check_invariants_cmd(
    ctx: click.Context,
    runner_dir: str | None,
    hub_dir: str | None,
    allow_external_db: bool,
    hub_url: str | None,
    directory: str | None,
    runner_url: str | None,
) -> None:
    """Check a runner's store, a hub's store, or both for broken invariants.

    Offline, against stopped stores: ``--runner-dir`` and/or ``--hub-dir``. Client, against running daemons:
    ``--hub-url`` (or ``$BZ_HUB_URL``) for the hub, ``--dir`` (or ``$BZ_RUNNER_DIR``) or ``--runner-url`` (or
    ``$BZ_RUNNER_URL``) for the host; the two kinds of option never mix. Exit 0 when every invariant holds;
    exit 1 listing each violation's slug and detail, or naming a daemon that could not be asked.
    """
    offline = runner_dir is not None or hub_dir is not None or allow_external_db
    client_on_commandline = [
        flag
        for param, flag in (("hub_url", "--hub-url"), ("directory", "--dir"), ("runner_url", "--runner-url"))
        if ctx.get_parameter_source(param) == ParameterSource.COMMANDLINE
    ]
    if offline:
        if client_on_commandline:
            raise click.UsageError(
                f"{', '.join(client_on_commandline)} ask running daemons and cannot be mixed with "
                "--runner-dir, --hub-dir or --allow-external-db, which read stopped stores"
            )
        _check_offline(runner_dir, hub_dir, allow_external_db)
        return
    if hub_url is None and directory is None and runner_url is None:
        raise click.UsageError(
            "pass --runner-dir and/or --hub-dir to check stopped stores, or --hub-url and/or --dir/--runner-url "
            "to ask running daemons"
        )
    _check_running(ctx, hub_url, directory, runner_url)


def _check_offline(runner_dir: str | None, hub_dir: str | None, allow_external_db: bool) -> None:
    registry = packaged_flag_registry()
    strict = strict_config()

    try:
        runner_db = (
            HostConfig.load_for_store(Path(runner_dir), flag_registry=registry, strict=strict).db_url
            if runner_dir is not None
            else None
        )
    except RunnerConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        hub_config = (
            HubConfig.load(Path(hub_dir), allow_external_db=allow_external_db, flag_registry=registry, strict=strict)
            if hub_dir is not None
            else None
        )
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    hub_db = hub_config.db_url if hub_config is not None else None

    # An operator runs this long after any crash recovery has finished.
    violations = Invariants(runner_db_url=runner_db, hub_db_url=hub_db).run(after_recovery=True)
    _report(violations)


def _report(violations: list[Violation], failures: list[str] | None = None) -> None:
    """Print the verdict: ``invariants hold`` only when nothing was found and nothing failed to run."""
    failures = failures or []
    if not violations and not failures:
        click.echo("invariants hold")
        return
    for violation in violations:
        click.echo(str(violation), err=True)
    problems = [*failures, *([f"{len(violations)} invariant violation(s)"] if violations else [])]
    raise click.ClickException("\n".join(problems))


def _check_running(ctx: click.Context, hub_url: str | None, directory: str | None, runner_url: str | None) -> None:
    """Ask each named daemon, so one that fails does not hide the other's verdict."""
    client_factory = CliCollaborators.of(ctx).client_factory
    violations: list[Violation] = []
    failures: list[str] = []
    asks: list[Callable[[], list[Violation]]] = []
    if hub_url is not None:
        asks.append(lambda: _ask_hub(ctx, client_factory, hub_url))
    if directory is not None or runner_url is not None:
        asks.append(lambda: _ask_host(client_factory, directory, runner_url))
    for ask in asks:
        try:
            violations.extend(ask())
        except click.UsageError:
            raise
        except click.ClickException as exc:
            failures.append(exc.message)
    _report(violations, failures)


def _ask_hub(ctx: click.Context, client_factory: ClientFactory, hub_url: str) -> list[Violation]:
    where = f"hub at {hub_url}"
    session: IReadSessionStore = HubCliCollaborators.of(ctx).sessions
    cli = CliContext.of(hub_url, session, OperatorTrace.source(ctx), client_factory)
    try:
        resp = cli.send("get", _HUB_PATH, timeout=CHECK_TIMEOUT)
        cli.check(resp, "check-invariants", on_status={HTTPStatus.CONFLICT: _RUNNING})
        return _violations(resp.json())
    except click.ClickException as exc:
        raise click.ClickException(f"{where}: {exc.message}") from exc
    except ValueError as exc:
        raise click.ClickException(f"{where}: unreadable answer ({exc})") from exc


def _ask_host(client_factory: ClientFactory, directory: str | None, runner_url: str | None) -> list[Violation]:
    """Reached as the runner verbs reach it: ``--runner-url`` over TCP when it outranks ``--dir``, else the socket."""
    dir_source = ParamSource.of("directory")
    url_source = ParamSource.of("runner_url")
    if dir_source.on_commandline and url_source.on_commandline:
        raise click.UsageError(
            "--dir and --runner-url are mutually exclusive: --dir names the socket, --runner-url TCP"
        )
    if runner_url is not None and (directory is None or url_source > dir_source):
        where = f"runner host at {runner_url}"
        client = client_factory(base_url=runner_url, timeout=CHECK_TIMEOUT)
    else:
        assert directory is not None
        sock = HostLayout(Path(directory)).socket_path
        where = f"runner host at {sock}"
        if not sock.exists():
            raise click.ClickException(f"{where}: no runner daemon is serving there")
        client = client_factory(base_url="http://runner", timeout=CHECK_TIMEOUT, uds=str(sock))
    try:
        with client:
            resp = client.get(_HOST_PATH)
        if resp.status_code == HTTPStatus.CONFLICT:
            raise click.ClickException(_RUNNING)
        resp.raise_for_status()
        return _violations(resp.json())
    except HTTPError as exc:
        raise click.ClickException(f"{where}: could not be reached or refused ({exc})") from exc
    except click.ClickException as exc:
        raise click.ClickException(f"{where}: {exc.message}") from exc
    except ValueError as exc:
        raise click.ClickException(f"{where}: unreadable answer ({exc})") from exc


def _violations(body: object) -> list[Violation]:
    parsed = InvariantsResponse.model_validate(body)
    return [Violation(invariant=v.invariant, detail=v.detail) for v in parsed.violations]

"""``blizzard runner add`` / ``retire`` / ``ingest`` — add a runner to a registered host, retire one from it, under the
host's own credential, or take one in from another store. ``add`` and ``retire`` reach a running host through its
routes, and write a stopped host's store directly; neither writes ``.env`` or the host file."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import click
from pydantic import TypeAdapter

from blizzard.cli.collaborators import CliCollaborators, Response
from blizzard.cli.refusal import refusal_of
from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import dto
from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import StoreBelowFloorError
from blizzard.runner.cli.daemon import Door, RunnerDaemon, daemon_holding
from blizzard.runner.cli.directory import load_host
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.cli.runtime import (
    InitCollaborators,
    host_answers,
    read_recorded_host,
    seed_host_records,
    seed_hosted_runners,
)
from blizzard.runner.cli.store_guard import (
    StoreVerdict,
    hosted_runner_ids,
    resolve_runner,
    store_refusal,
    store_write_guard,
)
from blizzard.runner.composition import build_hosted_runner_service, build_runner_ingest
from blizzard.runner.config import SCAFFOLD_AGENTS, ProjectBinding
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import SCAFFOLD_SUBSCRIPTION, HostConfig
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hosting.model import CLI, OPERATOR
from blizzard.runner.hosting.service import HostRunnerHubRefused, NewRunner, RunnerSettingsInvalid
from blizzard.runner.hub.client import (
    HostRefusalReason,
    HostRefused,
    HostRunnerRetireRefused,
    HubClientError,
    RunnerAddRefused,
)
from blizzard.runner.hub.token_file import ENV_FILENAME
from blizzard.runner.transfer.ingest import IngestRequest
from blizzard.runner.transfer.refusals import IngestRefused
from blizzard.wire.runner_host import HostRunnerListResponse, HostRunnerRetiredView, HostRunnerView

_DIR_OPTION = click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Host directory (overrides $BZ_RUNNER_DIR).",
)


@click.command("add")
@click.argument("name")
@_DIR_OPTION
@click.option(
    "--project",
    "projects",
    multiple=True,
    required=True,
    metavar="PROJECT",
    help="A project the runner serves, by id or slug; repeat for several.",
)
@click.option(
    "--workspace",
    "workspaces",
    multiple=True,
    metavar="WORKSPACE",
    help="The host's workspace each --project runs in: once for all of them, or once per --project in order. "
    "Default: the host's only workspace.",
)
@click.option(
    "--agents",
    "agents",
    multiple=True,
    type=click.IntRange(min=1),
    metavar="N",
    help=f"How many agents the runner runs at once, shared by every --project: once for the count, or once per "
    f"--project to sum them into it. Default: {SCAFFOLD_AGENTS}.",
)
@click.pass_context
def add(
    ctx: click.Context,
    name: str,
    directory: str,
    projects: tuple[str, ...],
    workspaces: tuple[str, ...],
    agents: tuple[int, ...],
) -> None:
    """Add runner NAME to this registered host at its hub and record it in the host store, through a running host's
    socket or into a stopped host's store; neither `.env` nor the host file is written.

    Refused, with nothing added, on a host holding no host token, when NAME is another runner's, or when a
    --workspace is not one the host holds an active record of."""
    config = load_host(directory)
    token = _host_token(config)
    door = Door.of(ctx)
    if host_answers(config.layout.socket_path, door):
        bindings, settings = _new_runner(read_recorded_host(config), projects, workspaces, agents)
        _add_on_running_host(config, door, name, bindings, settings)
        return
    clock = CliCollaborators.of(ctx).wall_clock
    hub = config.hub_url
    with store_write_guard(config, action=f"add runner {name}", door=door, clock=clock, refuse_foreign=True):
        # The file's runners hosted first, as the host's next start would, so the new runner's hosted row leaves none
        # of them unhosted; its workspaces and subscriptions seeded, so the runner is judged against their records.
        seed_hosted_runners(config, clock=clock)
        recorded = seed_host_records(config, clock=clock)
        bindings, settings = _new_runner(recorded, projects, workspaces, agents)
        new = NewRunner.of(
            name,
            [(b.project, b.workspace) for b in bindings],
            settings,
            workspaces=[workspace.id for workspace in recorded.workspaces],
        )
        engine = create_engine_from_url(config.db_url)
        try:
            with InitCollaborators.of(ctx).host_clients(hub) as host_clients:
                service = build_hosted_runner_service(recorded, engine, host_clients(token), clock=clock)
                try:
                    added = service.add(new, actor=OPERATOR, door=CLI)
                except RunnerSettingsInvalid as exc:
                    raise click.ClickException(f"runner {name} was not added: {_problems(exc.problems)}") from exc
                except HostRunnerHubRefused as exc:
                    raise click.ClickException(_host_refused(exc.refused, hub, f"add runner {name}")) from exc
                except RunnerAddRefused as exc:
                    raise click.ClickException(f"the hub at {hub} refused to add runner {name}: {exc.detail}") from exc
                except HubClientError as exc:
                    raise click.ClickException(f"cannot add runner {name} at the hub at {hub}: {exc}") from exc
        finally:
            engine.dispose()
    click.echo(
        f"added runner {name} ({added.runner_id}) at {hub} into the stopped host's store at {config.db_url}; it "
        "starts at the host's next start"
    )


def _new_runner(
    host: HostConfig, projects: tuple[str, ...], workspaces: tuple[str, ...], agents: tuple[int, ...]
) -> tuple[tuple[ProjectBinding, ...], dict[str, object]]:
    """The new runner's bindings and first settings, judged against ``host``'s workspace and subscription records."""
    try:
        bindings = host.bindings_for(projects, workspaces)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    settings: dict[str, object] = {"agents": _runner_agents(projects, agents)}
    if SCAFFOLD_SUBSCRIPTION.slug in {subscription.slug for subscription in host.subscriptions}:
        settings["subscription"] = SCAFFOLD_SUBSCRIPTION.slug
    return bindings, settings


def _add_on_running_host(
    config: HostConfig, door: Door, name: str, bindings: tuple[ProjectBinding, ...], settings: dict[str, object]
) -> None:
    body = {
        "name": name,
        "projects": [{"project": b.project, "workspace": b.workspace} for b in bindings],
        "settings": settings,
    }
    with RunnerDaemon.reach("add", str(config.root), None, door) as daemon:
        response = daemon.send_to_host("post", "/api/host/runners", json_body=body)
        if not response.is_success:
            raise click.ClickException(
                f"the running host at {daemon.where} did not add runner {name}: {_refused(response)}"
            )
        _, view = daemon.decode_answer(response, TypeAdapter(HostRunnerView))
        where = daemon.where
    click.echo(
        f"added runner {name} ({view.runner_id}) at {config.hub_url} through the running host at {where}; it starts "
        "the runner at its next tick"
    )


@click.command("retire")
@_DIR_OPTION
@click.option(
    "--runner",
    "runner",
    required=True,
    help="The host's runner to retire, by name or id; one the store holds retired, by id.",
)
@click.option("--force", is_flag=True, help="Retire it even while it holds chunks, releasing them.")
@click.pass_context
def retire(ctx: click.Context, directory: str, runner: str, force: bool) -> None:
    """Retire one of this registered host's runners at its hub, then record it retired in the host store. Retirement is
    final. Its identity, settings and history are kept. A running host is asked over its socket and drains the runner
    at its next tick; a stopped host's store is written directly. Neither `.env` nor the host file is written: a
    `[[runner]]` of a retired runner is no longer read, and the host warns of it until it is removed.

    Refused, with nothing retired, on a host holding no host token, or while the runner holds chunks unless --force
    releases them."""
    config = load_host(directory)
    token = _host_token(config)
    door = Door.of(ctx)
    if host_answers(config.layout.socket_path, door):
        _retire_on_running_host(config, door, runner, force)
        return
    clock = CliCollaborators.of(ctx).wall_clock
    target = _retiring(config, runner, clock)
    hub = config.hub_url
    with store_write_guard(
        config, action=f"retire runner {target.name}", door=door, clock=clock, refuse_foreign=False
    ) as verdict:
        _hosting(config, target, verdict, clock)
        engine = create_engine_from_url(config.db_url)
        try:
            with InitCollaborators.of(ctx).host_clients(hub) as host_clients:
                service = build_hosted_runner_service(config, engine, host_clients(token), clock=clock)
                held = service.held(target.runner_id)
                if held is None:
                    raise click.ClickException(f"the store does not hold runner {target.runner_id}")
                try:
                    retired = service.retire(held, force=force, actor=OPERATOR, door=CLI)
                except HostRunnerHubRefused as exc:
                    raise click.ClickException(
                        _host_refused(exc.refused, hub, f"retire runner {target.name} ({target.runner_id})")
                    ) from exc
                except HostRunnerRetireRefused as exc:
                    raise click.ClickException(
                        f"the hub refused to retire runner {target.name} ({target.runner_id}): {exc.detail}"
                    ) from exc
                except HubClientError as exc:
                    raise click.ClickException(
                        f"cannot retire runner {target.name} at the hub at {hub}: {exc}"
                    ) from exc
        finally:
            engine.dispose()
    if retired.already:
        click.echo(f"runner {target.name} ({target.runner_id}) is already retired in the stopped host's store")
    else:
        click.echo(
            f"retired runner {target.name} ({target.runner_id}) at {hub}{_released(retired.released_chunk_ids)}, and "
            "recorded it retired in the stopped host's store"
        )
    if target.configured:
        click.echo(f"its [[runner]] in {config.config_path} is no longer read — remove it")


def _retire_on_running_host(config: HostConfig, door: Door, runner: str, force: bool) -> None:
    with RunnerDaemon.reach("retire", str(config.root), None, door) as daemon:
        _, listed = daemon.decode_answer(daemon.get_from_host("/api/host/runners"), TypeAdapter(HostRunnerListResponse))
        matched = [
            item for item in listed.items if item.runner_id is not None and runner in (item.name, item.runner_id)
        ]
        if len(matched) != 1:
            named = ", ".join(f"{item.name} ({item.runner_id or 'no id'})" for item in listed.items) or "none"
            held = "holds several runners of that name" if matched else f"holds no runner {runner!r}"
            raise click.ClickException(f"the running host at {daemon.where} {held}: {named}")
        target = matched[0]
        response = daemon.send_to_host(
            "post", f"/api/host/runners/{target.runner_id}/retire", json_body={"force": force}
        )
        if not response.is_success:
            raise click.ClickException(
                f"the running host at {daemon.where} did not retire runner {target.name}: {_refused(response)}"
            )
        _, view = daemon.decode_answer(response, TypeAdapter(HostRunnerRetiredView))
        where = daemon.where
    if view.already:
        click.echo(f"runner {target.name} ({target.runner_id}) is already retired on the running host at {where}")
        return
    click.echo(
        f"retired runner {target.name} ({target.runner_id}) at {config.hub_url}{_released(view.released_chunk_ids)} "
        f"through the running host at {where}; it drains the runner at its next tick"
    )


def _hosting(config: HostConfig, target: _Retiring, verdict: StoreVerdict, clock: IClock) -> None:
    """Make the stopped host's store hold ``target`` before it is retired: the file's runners hosted as the host's next
    start would. A store judged foreign is not taken up, and refuses unless it already hosts the runner."""
    if verdict.foreign is not None:
        if target.runner_id in hosted_runner_ids(config, clock):
            return
        raise click.ClickException(store_refusal(verdict) or f"the store does not hold runner {target.runner_id}")
    seed_hosted_runners(config, clock=clock)


def _released(chunk_ids: Sequence[str]) -> str:
    return f", releasing {', '.join(chunk_ids)}" if chunk_ids else ""


def _problems(problems: Mapping[str, str]) -> str:
    return "; ".join(f"{key}: {why}" for key, why in sorted(problems.items()))


def _refused(response: Response) -> str:
    """The host's refusal, in words: its message — naming each failing setting — and its type."""
    refusal = refusal_of(response)
    kind = f" ({refusal.type})" if refusal.type else ""
    return f"{refusal.message or 'no reason given'}{kind}"


@dto
@dataclass(frozen=True)
class _Retiring:
    """The runner ``retire`` acts on: its name and hub id, and whether the host file still configures it."""

    name: str
    runner_id: str
    configured: bool


def _retiring(config: HostConfig, runner: str, clock: IClock) -> _Retiring:
    """The runner ``runner`` names: one the host file configures or the store hosts, by name or id, else one the store
    holds retired, by id — so its retire reports it retired already."""
    try:
        entry = resolve_runner(config, runner, clock)
    except ConfigError as exc:
        if HostLayout(config.root).store_path.exists() and runner in hosted_runner_ids(config, clock):
            return _Retiring(name=runner, runner_id=runner, configured=False)
        raise click.ClickException(str(exc)) from exc
    if entry.id is None:
        raise click.ClickException(
            f"runner {entry.name} has no id in {config.config_path}, so the hub holds no runner of it to retire; "
            "remove its [[runner]] by hand"
        )
    return _Retiring(name=entry.name, runner_id=entry.id, configured=any(e.id == entry.id for e in config.runners))


@click.command("ingest")
@click.argument("source", type=click.Path(path_type=Path))
@_DIR_OPTION
@click.option("--runner", "runner", required=True, help="The runner to take in, by name or id.")
@click.option(
    "--workspace-id",
    "workspace_id",
    default=None,
    help="The new id the runner's workspace takes here, when this host gives its id to another workspace.",
)
@click.pass_context
def ingest(ctx: click.Context, source: Path, directory: str, runner: str, workspace_id: str | None) -> None:
    """Move one runner from SOURCE — another host's directory, a `store reset --runner` archive, or a runner directory
    that has not graduated — into this host:
    its rows, its workspace, subscription and `[[runner]]` where this host lacks them, and its token; then delete its
    rows from SOURCE, moving a source left with no runner aside. An archive's `[[runner]]` and token must already be
    here. Refused while either host runs, or the hub does not hold the runner under this host; a re-run completes an
    interrupted ingest."""
    config = load_host(directory)
    door = Door.of(ctx)
    collaborators = InitCollaborators.of(ctx)
    with collaborators.host_clients(config.hub_url) as host_clients:
        service = build_runner_ingest(
            config,
            host_clients(config.host_token.value),
            holder=lambda sock: daemon_holding(sock, door),
            migrations=collaborators.store_migrations,
        )
        try:
            outcome = service.run(IngestRequest(source=source, runner=runner, workspace_id=workspace_id))
        except (IngestRefused, StoreBelowFloorError) as exc:
            raise click.ClickException(str(exc)) from exc
        except ConfigError as exc:
            raise click.ClickException(f"cannot ingest runner {runner}: {exc}") from exc
    verb = "completed the ingest of" if outcome.resumed else "ingested"
    click.echo(f"{verb} runner {outcome.runner_name} ({outcome.runner_id}) from {source} into {config.root}")
    for moved in outcome.set_aside:
        click.echo(f"set aside {moved}")


def _host_token(config: HostConfig) -> str:
    """The host's own token, or the refusal naming how a host gets one."""
    if not config.host_token.value:
        raise click.ClickException(
            f"this host holds no host token ({HOST_TOKEN_VARIABLE}), so it cannot add or retire runners at its hub: "
            f"register it with `blizzard runner init {config.root}`, or add it with "
            f"`blizzard hub runner-host add <name> --hub-url {config.hub_url}` and put the printed line in "
            f"{config.root / ENV_FILENAME}"
        )
    return config.host_token.value


def _runner_agents(projects: tuple[str, ...], agents: tuple[int, ...]) -> int:
    """The runner's agent count: the one --agents, or the sum of one per --project."""
    if not agents:
        return SCAFFOLD_AGENTS
    if len(agents) == 1:
        return agents[0]
    if len(agents) != len(projects):
        raise click.ClickException(f"give --agents once, or once per --project ({len(projects)})")
    return sum(agents)


def _host_refused(answer: HostRefused, hub: str, act: str) -> str:
    if answer.reason is HostRefusalReason.NOT_FOUND:
        return (
            f"the hub at {hub} answered not-found, so it did not {act}: the runner does not stand under this host, "
            f"or the hub predates hosts ({answer.detail})"
        )
    return (
        f"the hub at {hub} refuses this host's token, so it did not {act}: re-register the host with "
        f"`blizzard runner init`, or mint it a new token with `blizzard hub runner-host enroll <host_id> --hub-url "
        f"{hub}` ({answer.detail})"
    )

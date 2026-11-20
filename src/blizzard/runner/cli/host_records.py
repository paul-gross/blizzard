"""``blizzard runner workspace`` / ``subscription`` — the host's workspace and subscription records: create, list,
get, edit, retire and enable. A running host is asked through its routes; a stopped host's store is written directly,
holding the host's directory. Neither writes the host file."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import click
from pydantic import BaseModel, TypeAdapter, ValidationError

from blizzard.cli.collaborators import CliCollaborators, Response
from blizzard.cli.get_group import GetGroup
from blizzard.cli.refusal import refusal_of
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import collaborator
from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.api.host_records import subscription_list, workspace_view
from blizzard.runner.cli.daemon import Door, RunnerDaemon
from blizzard.runner.cli.directory import load_host
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.cli.runtime import host_answers, read_host_stores, seed_host_records
from blizzard.runner.cli.store_guard import store_write_guard
from blizzard.runner.composition import (
    build_host_record_services,
    build_host_stores,
    held_records,
    host_settings_reader,
)
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import HostConfig, decode_host_text
from blizzard.runner.host_records.model import HostRecord, RecordExists, RecordKind, RecordUnknown
from blizzard.runner.host_records.service import HostRecordService
from blizzard.runner.hosting.model import CLI, OPERATOR
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.wire.runner_host import (
    HostSubscriptionCreateRequest,
    HostSubscriptionPatchRequest,
    HostWorkspaceCreateRequest,
    HostWorkspacePatchRequest,
)

_DIR_OPTION = click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Host directory (overrides $BZ_RUNNER_DIR).",
)


@collaborator
@dataclass(frozen=True)
class _Kind:
    """One record kind's CLI surface: its routes, its write models and its words."""

    kind: RecordKind
    path: str
    create: type[HostWorkspaceCreateRequest] | type[HostSubscriptionCreateRequest]
    patch: type[HostWorkspacePatchRequest] | type[HostSubscriptionPatchRequest]

    def route(self, key: str | None = None, verb: str | None = None) -> str:
        path = self.path if key is None else f"{self.path}/{quote(key, safe='')}"
        return path if verb is None else f"{path}/{verb}"


WORKSPACE = _Kind(RecordKind.WORKSPACE, "/api/host/workspaces", HostWorkspaceCreateRequest, HostWorkspacePatchRequest)
_SUBSCRIPTION = _Kind(
    RecordKind.SUBSCRIPTION, "/api/host/subscriptions", HostSubscriptionCreateRequest, HostSubscriptionPatchRequest
)


def _settings(pairs: tuple[str, ...]) -> dict[str, object]:
    """Each ``KEY=VALUE`` of ``pairs``, its ``VALUE`` a TOML literal."""
    settings: dict[str, object] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise click.BadParameter(f"{pair!r} is not KEY=VALUE", param_hint="KEY=VALUE")
        try:
            parsed = decode_host_text(f"value = {value}", what=key.strip())["value"]
            json.dumps(parsed, allow_nan=False)
        except (ConfigError, ValueError, TypeError) as error:
            raise click.BadParameter(str(error), param_hint="KEY=VALUE") from error
        settings[key.strip()] = parsed
    return settings


def _validated(model: type[BaseModel], body: Mapping[str, object]) -> Any:
    """``body`` as ``model`` takes it, refusing what the route would."""
    try:
        return model.model_validate(dict(body))
    except ValidationError as error:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'body'}: {e['msg']}" for e in error.errors())
        raise click.BadParameter(problems, param_hint="KEY=VALUE") from error


def _echo(value: object) -> None:
    click.echo(json.dumps(value, indent=2, ensure_ascii=False))


def _refused(response: Response) -> str:
    """The host's refusal, in words: its message, each failing setting, and its type."""
    refusal = refusal_of(response)
    message = refusal.message or "no reason given"
    errors = refusal.members.get("errors")
    if errors:
        message += f": {json.dumps(errors)}"
    return f"{message} ({refusal.type})" if refusal.type else message


def _answer(daemon: RunnerDaemon, response: Response, what: str) -> dict[str, Any]:
    if not response.is_success:
        raise click.ClickException(f"the running host at {daemon.where} did not {what}: {_refused(response)}")
    return daemon.decode_answer(response, TypeAdapter(dict[str, Any]))[1]


class _Host:
    """The host a verb acts on: running, reached over its socket, or stopped, its store written directly."""

    def __init__(self, ctx: click.Context, directory: str) -> None:
        self.ctx = ctx
        self.directory = directory
        self.config: HostConfig = load_host(directory)
        self.door = Door.of(ctx)
        self.running = host_answers(self.config.layout.socket_path, self.door)

    def views(self, kind: _Kind, *, retired: bool, key: str | None = None) -> list[dict[str, Any]]:
        """The stopped host's records of ``kind`` — only ``key``'s when given, the retired ones only when
        ``retired`` — as its routes view them once its next start seeds its file's tables; a stopped host's runners
        spend nothing, so no subscription names a spender."""
        with read_host_stores(self.config) as stores:
            records = [
                record
                for record in held_records(self.config, stores, kind.kind)
                if (key is None or record.key == key) and (retired or not record.retired)
            ]
            if kind.kind is RecordKind.WORKSPACE:
                views: list[BaseModel] = [workspace_view(record) for record in records]
            else:
                views = list(subscription_list(records, stores.external_usage, {}).items)
        return [view.model_dump(mode="json") for view in views]

    def write(
        self, kind: _Kind, what: str, act: Callable[[HostRecordService], tuple[HostRecord, HostRecord | None]]
    ) -> tuple[HostRecord, HostRecord | None]:
        """``act`` on the stopped host's ``kind`` service, holding its directory, the file's records seeded first as
        its next start would; ``act`` answers the record as written and as it stood before (``None`` for a
        create)."""
        clock = CliCollaborators.of(self.ctx).wall_clock
        with store_write_guard(self.config, action=what, door=self.door, clock=clock, refuse_foreign=True):
            seed_host_records(self.config, clock=clock)
            engine = create_engine_from_url(self.config.db_url)
            try:
                stores = build_host_stores(
                    engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), clock=clock
                )
                reader = host_settings_reader(self.config, stores)
                service = build_host_record_services(reader, stores, clock=clock)[kind.kind]
                try:
                    return act(service)
                except (RecordUnknown, RecordExists, SettingsRevisionConflict) as exc:
                    raise click.ClickException(f"did not {what}: {exc}") from exc
                except SettingsInvalid as exc:
                    problems = "; ".join(f"{key}: {why}" for key, why in sorted(exc.errors.items()))
                    raise click.ClickException(f"did not {what}: {problems}") from exc
            finally:
                engine.dispose()

    def reach(self, verb: str) -> Any:
        return RunnerDaemon.reach(verb, self.directory, None, self.door)


def _report(kind: _Kind, host: _Host, done: str, key: str, revision: int, *, wrote: bool, where: str) -> None:
    if not wrote:
        stands = "already stands so" if done == "edited" else f"is {done} already"
        click.echo(f"{kind.kind} {key} {stands} at revision {revision}; nothing was written")
        return
    if host.running:
        click.echo(f"{kind.kind} {key} {done} at revision {revision} through the running host at {where}")
        return
    click.echo(
        f"{kind.kind} {key} {done} at revision {revision} in the stopped host's store at {host.config.db_url}; the "
        "host takes it up at its next start"
    )


def edit_record(
    ctx: click.Context, kind: _Kind, directory: str, key: str, body: Mapping[str, object], *, verb: str
) -> None:
    """Sparse-merge ``body`` — a ``None`` value returning its key to its default — into ``kind`` record ``key`` at
    the revision it is read at, through the running host or into the stopped host's store, and say which."""
    request = _validated(kind.patch, body)
    host = _Host(ctx, directory)
    what = f"edit {kind.kind} {key}"
    if host.running:
        with host.reach(verb) as daemon:
            before = _answer(daemon, daemon.send_to_host("get", kind.route(key)), what)
            view = _answer(
                daemon,
                daemon.send_to_host(
                    "patch", kind.route(key), json_body=dict(body), headers={"If-Match": str(before["revision"])}
                ),
                what,
            )
            where = daemon.where
        revision = int(view["revision"])
        _report(kind, host, "edited", key, revision, wrote=revision != before["revision"], where=where)
        return
    sets, cleared = request.stated()

    def act(service: HostRecordService) -> tuple[HostRecord, HostRecord | None]:
        held = service.held(key)
        return service.edit(held, sets, cleared, if_match=held.revision, actor=OPERATOR, door=CLI), held

    record, held = host.write(kind, what, act)
    _report(
        kind, host, "edited", key, record.revision, wrote=held is None or record.revision != held.revision, where=""
    )


def _record_group(kind: _Kind, name: str, metavar: str, noun: str) -> click.Group:
    @click.group(name, cls=GetGroup, help=f"Create, read, edit, retire and enable this host's {noun}s.")
    def group() -> None:
        pass

    @group.command(
        "create",
        help=f"Create {noun} {metavar} from KEY=VALUE settings, each VALUE a TOML literal, "
        f"named as its [[{kind.kind}]] table names them; judged as the host file is at boot.",
    )
    @click.argument("key", metavar=metavar)
    @click.argument("pairs", nargs=-1, metavar="[KEY=VALUE]...")
    @_DIR_OPTION
    @click.pass_context
    def create(ctx: click.Context, key: str, pairs: tuple[str, ...], directory: str) -> None:
        body = {kind.kind.key_name: key, **_settings(pairs)}
        request = _validated(kind.create, body)
        host = _Host(ctx, directory)
        what = f"create {kind.kind} {key}"
        if host.running:
            with host.reach(f"{name} create") as daemon:
                view = _answer(daemon, daemon.send_to_host("post", kind.route(), json_body=body), what)
                where = daemon.where
            _report(kind, host, "created", key, int(view["revision"]), wrote=True, where=where)
            return
        sets, _ = request.stated()
        record, _ = host.write(kind, what, lambda service: (service.create(key, sets, actor=OPERATOR, door=CLI), None))
        _report(kind, host, "created", record.key, record.revision, wrote=True, where="")

    @group.command("list", help=f"List this host's {noun}s; --retired lists the retired ones too.")
    @click.option("--retired", is_flag=True, help="Include retired records.")
    @_DIR_OPTION
    @click.pass_context
    def list_(ctx: click.Context, retired: bool, directory: str) -> None:
        host = _Host(ctx, directory)
        if host.running:
            with host.reach(f"{name} list") as daemon:
                suffix = "?retired=true" if retired else ""
                _echo(_answer(daemon, daemon.send_to_host("get", kind.route() + suffix), f"list {noun}s"))
            return
        _echo({"items": host.views(kind, retired=retired)})

    @group.command("get", help=f"Print {noun} {metavar}, retired or not.")
    @click.argument("key", metavar=metavar)
    @_DIR_OPTION
    @click.pass_context
    def get(ctx: click.Context, key: str, directory: str) -> None:
        host = _Host(ctx, directory)
        if host.running:
            with host.reach(f"{name} get") as daemon:
                _echo(_answer(daemon, daemon.send_to_host("get", kind.route(key)), f"read {kind.kind} {key}"))
            return
        views = host.views(kind, retired=True, key=key)
        if not views:
            raise click.ClickException(f"the stopped host's store holds no {kind.kind} {key!r}")
        _echo(views[0])

    @group.command(
        "edit",
        help=f"Set each KEY=VALUE of {noun} {metavar}, each VALUE a TOML literal, and return each "
        "--unset KEY to its default; every other setting stands. A retired record stays retired.",
    )
    @click.argument("key", metavar=metavar)
    @click.argument("pairs", nargs=-1, metavar="[KEY=VALUE]...")
    @click.option("--unset", "unsets", multiple=True, metavar="KEY", help="Return KEY to its default; repeatable.")
    @_DIR_OPTION
    @click.pass_context
    def edit(ctx: click.Context, key: str, pairs: tuple[str, ...], unsets: tuple[str, ...], directory: str) -> None:
        body: dict[str, object] = {**_settings(pairs), **dict.fromkeys(unsets)}
        if not body:
            raise click.UsageError("give a KEY=VALUE to set or an --unset KEY")
        edit_record(ctx, kind, directory, key, body, verb=f"{name} edit")

    def lifecycle(verb: str, done: str, help_text: str) -> None:
        @group.command(verb, help=help_text)
        @click.argument("key", metavar=metavar)
        @_DIR_OPTION
        @click.pass_context
        def command(ctx: click.Context, key: str, directory: str) -> None:
            host = _Host(ctx, directory)
            what = f"{verb} {kind.kind} {key}"
            if host.running:
                with host.reach(f"{name} {verb}") as daemon:
                    before = _answer(daemon, daemon.send_to_host("get", kind.route(key)), what)
                    view = _answer(daemon, daemon.send_to_host("post", kind.route(key, verb)), what)
                    where = daemon.where
                revision = int(view["revision"])
                _report(kind, host, done, key, revision, wrote=revision != before["revision"], where=where)
                return

            def act(service: HostRecordService) -> tuple[HostRecord, HostRecord | None]:
                held = service.held(key)
                op = service.retire if verb == "retire" else service.enable
                return op(held, actor=OPERATOR, door=CLI), held

            record, held = host.write(kind, what, act)
            wrote = held is None or record.revision != held.revision
            _report(kind, host, done, key, record.revision, wrote=wrote, where="")

    lifecycle(
        "retire",
        "retired",
        f"Retire {noun} {metavar}, keeping its record and history; the host stops serving it. "
        "Of a retired one, writes nothing.",
    )
    lifecycle("enable", "enabled", f"Reverse the retirement of {noun} {metavar}. Of an enabled one, writes nothing.")
    return group


workspace_group = _record_group(WORKSPACE, "workspace", "ID", "workspace")
subscription_group = _record_group(_SUBSCRIPTION, "subscription", "SLUG", "subscription")

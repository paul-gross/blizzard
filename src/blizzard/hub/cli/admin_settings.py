"""Hub-wide operational settings, independent of any tenant."""

from __future__ import annotations

import json
import math
from datetime import date, time
from pathlib import Path
from typing import Any, cast

import click
from pydantic import TypeAdapter, ValidationError

from blizzard.cli.get_group import GetGroup
from blizzard.hub.cli.command import AdminCommand
from blizzard.hub.cli.config import ApplyListing
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing
from blizzard.hub.documents import (
    TOML_CODEC,
    ConfigDecodeError,
    ConfigEncodeError,
    accepted_extensions,
    codec_for_path,
    decode_toml_value,
)
from blizzard.wire.config import ConfigApplyResponse
from blizzard.wire.hub_settings import (
    SETTINGS_EXPORT_ADAPTER,
    HubSettingRow,
    HubSettingsChange,
    HubSettingsChanges,
    HubSettingsView,
)

_PATH = "/api/admin/settings"

_SETTINGS = TypeAdapter(HubSettingsView)
_APPLIED = TypeAdapter(ConfigApplyResponse)
_CHANGES = TypeAdapter(HubSettingsChanges)


def _json_value(value: object, *, what: str, parents: set[int] | None = None) -> None:
    parents = parents if parents is not None else set()
    identity = id(value)
    if isinstance(value, (date, time)):
        raise click.ClickException(f"{what} contains a TOML date or time; quote it to use a string instead")
    if isinstance(value, float) and not math.isfinite(value):
        raise click.ClickException(f"{what} contains a non-finite number, which JSON cannot represent")
    if isinstance(value, (dict, list)):
        if identity in parents:
            raise click.ClickException(f"{what} contains a cycle, which JSON cannot represent")
        parents.add(identity)
        if isinstance(value, dict):
            mapping = cast(dict[object, object], value)
            if any(not isinstance(key, str) for key in mapping):
                raise click.ClickException(f"{what} contains a non-string key, which JSON cannot represent")
            items = mapping.values()
        else:
            items = cast(list[object], value)
        for item in items:
            _json_value(item, what=what, parents=parents)
        parents.remove(identity)
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise click.ClickException(f"{what} contains a value which JSON cannot represent")


class SettingsListing(Listing):
    empty = "no settings"

    def line(self, row: Any) -> str:
        setting = cast(HubSettingRow, row)
        return f"{setting.key} = {json.dumps(setting.value)}  ({setting.source}, {setting.lifetime})"


class ChangesListing(Listing):
    empty = "no settings changes yet"

    def line(self, row: Any) -> str:
        change = cast(HubSettingsChange, row)
        return f"#{change.id}  {change.recorded_at}  {change.actor}  {change.door}  r{change.revision}  {change.op}"


@click.group("settings", cls=GetGroup)
def admin_settings_group() -> None:
    """Show or edit the hub's operational settings."""


def _show(cli: CliContext, raw: Any, view: HubSettingsView) -> None:
    cli.show(raw, SettingsListing(view.settings))


@admin_settings_group.command("get", cls=AdminCommand)
@click.argument("key", required=False)
def settings_get(cli: CliContext, key: str | None) -> None:
    """Show all settings, or the row for KEY."""
    raw, view, pairs = cli.decode_rows(
        _PATH, "GET /admin/settings", page=_SETTINGS, rows=lambda p: p.settings, key="settings"
    )
    if key is None:
        _show(cli, raw, view)
        return
    match = next(((raw_row, row) for raw_row, row in pairs if row.key == key), None)
    if match is None:
        raise click.ClickException(f"no setting named {key}")
    cli.show(match[0], SettingsListing([match[1]]))


def _write(cli: CliContext, body: dict[str, object]) -> None:
    _, current = cli.decode(cli.get(_PATH, "GET /admin/settings"), _SETTINGS, "GET /admin/settings")
    response = cli.patch(_PATH, "PATCH /admin/settings", json_body=body, if_match=current.revision, door=True)
    raw, view = cli.decode(response, _SETTINGS, "PATCH /admin/settings")
    _show(cli, raw, view)


@admin_settings_group.command("set", cls=AdminCommand)
@click.argument("key")
@click.argument("value")
def settings_set(cli: CliContext, key: str, value: str) -> None:
    """Set KEY to VALUE, a TOML value."""
    try:
        parsed = decode_toml_value(value)
    except ConfigDecodeError as exc:
        raise click.ClickException(f"VALUE is not valid TOML: {exc}") from exc
    _json_value(parsed, what="VALUE")
    _write(cli, {"set": {key: parsed}, "unset": []})


@admin_settings_group.command("unset", cls=AdminCommand)
@click.argument("key")
def settings_unset(cli: CliContext, key: str) -> None:
    """Return KEY to its default."""
    _write(cli, {"set": {}, "unset": [key]})


@admin_settings_group.command("changes", cls=AdminCommand)
@click.option("--all", "everything", is_flag=True, help="Print every change rather than the newest page.")
def settings_changes(cli: CliContext, everything: bool) -> None:
    """List settings changes, newest first."""
    rows, _ = cli.get_before_pages(
        f"{_PATH}/changes",
        "GET /admin/settings/changes",
        page=_CHANGES,
        rows=lambda p: p.changes,
        key="changes",
        want=None,
        first_page_only=not everything,
    )
    cli.show({"changes": [raw for raw, _ in rows]}, ChangesListing([row for _, row in rows]))


@admin_settings_group.command("apply", cls=AdminCommand)
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Show what would change and write nothing.")
def settings_apply(cli: CliContext, file: Path, dry_run: bool) -> None:
    """Apply a settings document FILE."""
    codec = codec_for_path(file)
    if codec is None:
        raise click.UsageError(f"{file.name}: unknown extension; use one of {', '.join(accepted_extensions())}")
    try:
        document = codec.decode(file.read_bytes())
        _json_value(document, what=str(file))
        document = SETTINGS_EXPORT_ADAPTER.validate_python(document)
    except (ConfigDecodeError, ValidationError) as exc:
        raise click.ClickException(f"{file}: {exc}") from exc
    resp = cli.post(
        f"{_PATH}/apply",
        "POST /admin/settings/apply",
        json_body=document,
        params={"dry_run": "true"} if dry_run else None,
    )
    raw, applied = cli.decode(resp, _APPLIED, "POST /admin/settings/apply")
    cli.show(raw, ApplyListing(applied.outcomes))


@admin_settings_group.command("export", cls=AdminCommand)
def settings_export(cli: CliContext) -> None:
    """Export the settings document as TOML (or JSON with --json)."""
    resp = cli.get(f"{_PATH}/export", "GET /admin/settings/export")
    raw, document = cli.decode(resp, SETTINGS_EXPORT_ADAPTER, "GET /admin/settings/export")
    if cli.as_json:
        cli.show_lines(raw)
    else:
        try:
            encoded = TOML_CODEC.encode(document)
        except ConfigEncodeError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(encoded.decode("utf-8"), nl=False)

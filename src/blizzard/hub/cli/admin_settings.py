"""Hub-wide operational settings, independent of any tenant."""

from __future__ import annotations

import json
import math
from datetime import date, time
from pathlib import Path
from typing import Any, cast

import click

from blizzard.cli.get_group import GetGroup
from blizzard.foundation.toml_emit import json_literal
from blizzard.hub.cli.command import AdminCommand
from blizzard.hub.cli.config import ApplyListing
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing
from blizzard.hub.config import ConfigError, HubConfig

_PATH = "/api/admin/settings"


def _json_value(value: object, *, what: str) -> None:
    if isinstance(value, (date, time)):
        raise click.ClickException(f"{what} contains a TOML date or time; quote it to use a string instead")
    if isinstance(value, float) and not math.isfinite(value):
        raise click.ClickException(f"{what} contains a non-finite number, which JSON cannot represent")
    if isinstance(value, dict):
        for item in cast(dict[str, object], value).values():
            _json_value(item, what=what)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            _json_value(item, what=what)


class SettingsListing(Listing):
    empty = "no settings"

    def line(self, row: Any) -> str:
        return f"{row['key']} = {json.dumps(row['value'])}  ({row['source']}, {row['lifetime']})"


class ChangesListing(Listing):
    empty = "no settings changes yet"

    def line(self, row: Any) -> str:
        return f"#{row['id']}  {row['recorded_at']}  {row['actor']}  {row['door']}  r{row['revision']}  {row['op']}"


@click.group("settings", cls=GetGroup)
def admin_settings_group() -> None:
    """Show or edit the hub's operational settings."""


def _show(cli: CliContext, body: Any) -> None:
    cli.show(body, SettingsListing(body["settings"]))


@admin_settings_group.command("get", cls=AdminCommand)
@click.argument("key", required=False)
def settings_get(cli: CliContext, key: str | None) -> None:
    """Show all settings, or the row for KEY."""
    body = cli.get(_PATH, "GET /admin/settings").json()
    if key is None:
        _show(cli, body)
        return
    row = next((row for row in body["settings"] if row["key"] == key), None)
    if row is None:
        raise click.ClickException(f"no setting named {key}")
    cli.show(row, SettingsListing([row]))


def _write(cli: CliContext, body: dict[str, object]) -> None:
    revision = cli.get(_PATH, "GET /admin/settings").json()["revision"]
    response = cli.patch(_PATH, "PATCH /admin/settings", json_body=body, if_match=revision, door=True)
    _show(cli, response.json())


@admin_settings_group.command("set", cls=AdminCommand)
@click.argument("key")
@click.argument("value")
def settings_set(cli: CliContext, key: str, value: str) -> None:
    """Set KEY to VALUE, a TOML value."""
    try:
        parsed = HubConfig.decode_text(f"value = {value}", what="VALUE")["value"]
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
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
        f"{_PATH}/changes", "GET /admin/settings/changes", key="changes", want=None, first_page_only=not everything
    )
    cli.show({"changes": rows}, ChangesListing(rows))


@admin_settings_group.command("apply", cls=AdminCommand)
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Show what would change and write nothing.")
def settings_apply(cli: CliContext, file: Path, dry_run: bool) -> None:
    """Apply a TOML settings document FILE."""
    try:
        document = HubConfig.decode_text(file.read_text(), what=str(file))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    _json_value(document, what=str(file))
    body = cli.post(
        f"{_PATH}/apply",
        "POST /admin/settings/apply",
        json_body=document,
        params={"dry_run": "true"} if dry_run else None,
    ).json()
    cli.show(body, ApplyListing(body["outcomes"]))


def _literal(value: object) -> str:
    if isinstance(value, dict):
        return (
            "{ "
            + ", ".join(
                f"{json.dumps(k, ensure_ascii=False)} = {_literal(v)}"
                for k, v in cast(dict[str, object], value).items()
            )
            + " }"
        )
    if isinstance(value, list):
        return "[" + ", ".join(_literal(item) for item in cast(list[object], value)) + "]"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return json_literal(value)


def _toml(document: dict[str, object], prefix: tuple[str, ...] = ()) -> list[str]:
    lines = [
        f"{json.dumps(key, ensure_ascii=False)} = {_literal(value)}"
        for key, value in document.items()
        if not isinstance(value, dict)
    ]
    for key, value in document.items():
        if isinstance(value, dict):
            path = (*prefix, key)
            lines.extend(
                [
                    "",
                    f"[{'.'.join(json.dumps(part, ensure_ascii=False) for part in path)}]",
                    *_toml(cast(dict[str, object], value), path),
                ]
            )
    return lines


@admin_settings_group.command("export", cls=AdminCommand)
def settings_export(cli: CliContext) -> None:
    """Export the settings document as TOML (or JSON with --json)."""
    document = cli.get(f"{_PATH}/export", "GET /admin/settings/export").json()
    if cli.as_json:
        cli.show_lines(document)
    else:
        click.echo("\n".join(_toml(document)).lstrip())

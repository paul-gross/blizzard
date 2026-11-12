"""``blizzard hub settings`` — the tenant's settings, read and written a key at a time: ``get [KEY]``,
``set KEY VALUE``, ``unset KEY`` and ``changes``."""

from __future__ import annotations

from typing import Any

import click

from blizzard.cli.get_group import GetGroup
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.config import ChangeListing
from blizzard.hub.cli.context import CliContext

#: The keys the configuration document spells under ``settings``. ``default_urgency`` is the only one.
_DEFAULT_URGENCY = "default_urgency"
_KEYS = (_DEFAULT_URGENCY,)


@click.group("settings", cls=GetGroup)
def settings_group() -> None:
    """Read or change the tenant's settings, a key at a time."""


def _known(key: str) -> str:
    if key not in _KEYS:
        raise click.UsageError(f"unknown setting {key!r}; the settings are: {', '.join(_KEYS)}")
    return key


def _value(body: Any, key: str) -> str:
    return str(body[key]["slug"])


def _record(body: Any) -> tuple[str, str]:
    return f"revision {body['revision']}", f"updated {body['updated_at']} by {body['updated_by']}"


def _show_key(cli: CliContext, body: Any, key: str) -> None:
    cli.show_lines({"key": key, "value": _value(body, key)}, f"{key} {_value(body, key)}")


@settings_group.command("get", cls=FleetCommand)
@click.argument("key", required=False, metavar="[KEY]")
def settings_get(cli: CliContext, key: str | None) -> None:
    """Print every tenant setting, or the row of KEY. `default_urgency` is what every new chunk takes when nothing
    names another."""
    if key is not None:
        _known(key)
    body = cli.get("/api/settings", "GET /settings").json()
    if key is not None:
        _show_key(cli, body, key)
        return
    cli.show_lines(body, *(f"{k} {_value(body, k)}" for k in _KEYS), *_record(body))


@settings_group.command("set", cls=FleetCommand)
@click.argument("key")
@click.argument("value")
def settings_set(cli: CliContext, key: str, value: str) -> None:
    """Set KEY to VALUE. No chunk moves; a new default decides only what is minted from then on."""
    _known(key)
    revision = cli.get("/api/settings", "GET /settings").json()["revision"]
    resp = cli.patch("/api/settings", "PATCH /settings", json_body={key: value}, if_match=revision, door=True)
    _show_key(cli, resp.json(), key)


@settings_group.command("unset", cls=FleetCommand)
@click.argument("key")
def settings_unset(cli: CliContext, key: str) -> None:
    """Refused: no setting has a default to return to."""
    _known(key)
    raise click.UsageError(
        f"{key} has no default to return to: every new chunk needs a live default urgency; use `set {key} SLUG`"
    )


@settings_group.command("changes", cls=FleetCommand)
@click.option(
    "--all", "everything", is_flag=True, default=False, help="Print every change rather than the newest page."
)
def settings_changes(cli: CliContext, everything: bool) -> None:
    """List the tenant settings' changes newest first — the rows `config changes --kind tenant_settings` prints."""
    rows, _ = cli.get_before_pages(
        "/api/config/changes",
        "GET /config/changes",
        key="changes",
        params={"record_kind": "tenant_settings"},
        want=None,
        first_page_only=not everything,
    )
    cli.show({"changes": rows}, ChangeListing(rows))

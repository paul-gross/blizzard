"""``blizzard hub secret`` — operator verbs over the write-only secret store.

``create`` and ``set`` read the value from stdin only, so it never appears in argv, shell history, or
the process list; no verb prints a value."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import click
from pydantic import TypeAdapter

from blizzard.cli.config_inputs import packaged_flag_registry, strict_config
from blizzard.cli.get_group import GetGroup
from blizzard.cli.refusal import refusal_of
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing
from blizzard.wire.secret import SECRET_ALREADY_EXISTS, SECRET_NOT_FOUND, SecretView

_SECRET = TypeAdapter(SecretView)
_SECRETS = TypeAdapter(list[SecretView])


class SecretListing(Listing):
    empty = "no secrets yet"

    def line(self, row: Any) -> str:
        secret = cast(SecretView, row)
        marker = "retired" if secret.retired else "enabled"
        return f"{secret.name}  r{secret.revision}  {marker}"


@click.group("secret", cls=GetGroup)
def secret_group() -> None:
    """Manage the write-only secret store."""


def _stdin_value() -> str:
    value = click.get_text_stream("stdin").read().rstrip("\r\n")
    if not value:
        raise click.ClickException("refusing an empty secret value — pipe the value on stdin")
    return value


@secret_group.command("create", cls=FleetCommand)
@click.argument("name")
def secret_create(cli: CliContext, name: str) -> None:
    """Store a new secret NAME, its value read from stdin — never an argument.

    Refused for a name that already exists — replace it with `secret set`. Trailing CR/LF is
    stripped; an empty value is refused."""
    value = _stdin_value()
    resp = cli.send("post", "/api/secrets", json_body={"name": name, "value": value}, door=True)
    refusal = refusal_of(resp)
    if refusal.type == SECRET_ALREADY_EXISTS:
        raise click.ClickException(
            f"{refusal.message or f'secret {name} already exists'} — replace it with `secret set`"
        )
    cli.check(resp, "POST /secrets")
    raw, secret = cli.decode(resp, _SECRET, "POST /secrets")
    cli.show_lines(raw, f"secret {name} created at revision {secret.revision}")


@secret_group.command("set", cls=FleetCommand)
@click.argument("name")
def secret_set(cli: CliContext, name: str) -> None:
    """Replace NAME's value, read from stdin — never an argument.

    Refused for a name that does not exist — create it with `secret create`. Trailing CR/LF is
    stripped; an empty value is refused."""
    value = _stdin_value()
    resp = cli.send("put", f"/api/secrets/{name}/value", json_body={"value": value}, door=True)
    if refusal_of(resp).type == SECRET_NOT_FOUND:
        raise click.ClickException(f"unknown secret {name} — create it with `secret create`")
    cli.check(resp, "PUT /secrets/{name}/value")
    raw, secret = cli.decode(resp, _SECRET, "PUT /secrets/{name}/value")
    cli.show_lines(raw, f"secret {name} set at revision {secret.revision}")


@secret_group.command("rotate-key")
@click.option(
    "--dir",
    "directory",
    default=".",
    envvar="BZ_HUB_DIR",
    help="Hub runtime directory (overrides $BZ_HUB_DIR).",
)
def secret_rotate_key(directory: str) -> None:
    """Re-seal every open tenant's secrets under the hub's current key — offline, on the hub host.

    Directory mode mints a new generation and promotes it once every tenant is re-sealed; env mode
    re-seals under $BZ_HUB_SECRET_KEY, opening older rows through $BZ_HUB_SECRET_KEY_PREVIOUS. A race
    with a replace leaves the old key current and exits non-zero — re-run it."""
    from blizzard.foundation.secrets.sealing import SecretRotationConflict
    from blizzard.hub.app import rotate_secret_keys
    from blizzard.hub.config import ConfigError, HubConfig

    try:
        result = rotate_secret_keys(
            HubConfig.load(Path(directory), flag_registry=packaged_flag_registry(), strict=strict_config()), os.environ
        )
    except (ConfigError, SecretRotationConflict) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"re-sealed {result.resealed} secret(s) under key generation {result.key_id}")


@secret_group.command("list", cls=FleetCommand)
@click.option("--include-retired", "include_retired", is_flag=True, default=False, help="Also list retired secrets.")
def secret_list(cli: CliContext, include_retired: bool) -> None:
    """List secrets — name, revision, retired. Never a value."""
    params = {"include_retired": "true"} if include_retired else None
    raw, secrets = cli.decode(cli.get("/api/secrets", "GET /secrets", params=params), _SECRETS, "GET /secrets")
    cli.show(raw, SecretListing(secrets))


@secret_group.command("get", cls=FleetCommand)
@click.argument("name")
def secret_show(cli: CliContext, name: str) -> None:
    """Show NAME's metadata — revision, who replaced it and when. Never its value."""
    raw, secret = cli.decode(cli.get(f"/api/secrets/{name}", "GET /secrets/{name}"), _SECRET, "GET /secrets/{name}")
    state = "retired" if secret.retired else "enabled"
    used_by = ", ".join(f"{r.kind} {r.label or r.key}" for r in secret.references) or "-"
    cli.show_lines(
        raw,
        f"{secret.name}  revision {secret.revision}  {state}",
        f"created {secret.created_at}",
        f"replaced {secret.replaced_at} by {secret.replaced_by}",
        f"used by {used_by}",
    )


@secret_group.command("retire", cls=FleetCommand)
@click.argument("name")
def secret_retire(cli: CliContext, name: str) -> None:
    """Retire NAME — a reversible brake; a retired secret cannot be replaced.

    Refused while an active record still uses it; the refusal names those records."""
    _set_lifecycle(cli, name, verb="retire")


@secret_group.command("enable", cls=FleetCommand)
@click.argument("name")
def secret_enable(cli: CliContext, name: str) -> None:
    """Re-enable a retired NAME."""
    _set_lifecycle(cli, name, verb="enable")


def _set_lifecycle(cli: CliContext, name: str, *, verb: str) -> None:
    operation = f"POST /secrets/{{name}}/{verb}"
    resp = cli.post(f"/api/secrets/{name}/{verb}", operation, door=True)
    raw, secret = cli.decode(resp, _SECRET, operation)
    state = "retired" if secret.retired else "enabled"
    cli.show_lines(raw, f"secret {name} is now {state}")

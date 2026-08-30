"""``blizzard hub item`` — blizzard#361: operator verbs over one work item."""

from __future__ import annotations

from pathlib import Path

import click
import httpx

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext


class WorkToken(click.ParamType):
    """A work-item token as typed (``hub:42``, ``blizzard#123``), parsed once here at
    the CLI edge into the plain ``(source, ref)`` path segments the wire carries — unlike
    ``Pointer``, the token form never rides an item route."""

    name = "token"

    def convert(self, value: str, param: click.Parameter | None, ctx: click.Context | None) -> tuple[str, str]:
        for sep in (":", "#"):
            if sep in value:
                source, _, ref = value.partition(sep)
                source, ref = source.strip(), ref.strip()
                if source and ref:
                    return source, ref
        self.fail(
            f"could not resolve {value!r} to a work source and ref (expected <source>:<ref> or <source>#<ref>)",
            param,
            ctx,
        )


_DEFAULT_ITEM_SOURCE = "hub"


@click.group("item")
def item_group() -> None:
    """Operator verbs over one work item: author, edit, or withdraw it at its source."""


def _read_body_file(path: str) -> str:
    """PATH's contents, or stdin when PATH is ``-`` (``graph mint`` precedent)."""
    if path == "-":
        return click.get_text_stream("stdin").read()
    try:
        return Path(path).read_text()
    except OSError as exc:
        raise click.ClickException(f"failed to read {path}: {exc}") from exc


@item_group.command("create", cls=FleetCommand)
@click.option("--title", required=True, help="The item's title.")
@click.option("--body-file", "body_file", required=True, help="Path to the item's body, or '-' for stdin.")
@click.option(
    "--priority",
    "priority",
    type=click.Choice(["low", "normal", "high"]),
    default="normal",
    help="Stated priority (default normal).",
)
@click.option(
    "--source",
    "source",
    default=_DEFAULT_ITEM_SOURCE,
    help=f"Work source to author at (default {_DEFAULT_ITEM_SOURCE!r}, the one source with an editor).",
)
def item_create(cli: CliContext, title: str, body_file: str, priority: str, source: str) -> None:
    """Author a fresh item at SOURCE, minting its resting chunk.

    --body-file may be '-' to read the body from stdin, so an agent can pipe a composed
    spec without shell-quoting a multi-line markdown document."""
    body = _read_body_file(body_file)
    resp = cli.send(
        "post",
        f"/api/work-sources/{source}/items",
        json_body={"title": title, "body": body, "stated_priority": priority},
    )
    if resp.status_code == httpx.codes.CONFLICT:
        conflict = resp.json()
        if "existing_chunk_id" in conflict:
            raise click.ClickException(
                f"pointer {conflict.get('source')}#{conflict.get('ref')} already held by "
                f"chunk {conflict.get('existing_chunk_id')}"
            )
    cli.check(
        resp,
        "POST /work-sources/{source}/items",
        on_status={404: f"unknown work source {source!r}", 409: f"work source {source!r} has no editor"},
    )
    body_json = resp.json()
    cli.show_lines(body_json, f"created {body_json['label']} → chunk {body_json['chunk_id']}")


@item_group.command("edit", cls=FleetCommand)
@click.argument("token", type=WorkToken())
@click.option("--title", default=None, help="Replace the title.")
@click.option("--body-file", "body_file", default=None, help="Replace the body from a path, or '-' for stdin.")
@click.option(
    "--priority", "priority", type=click.Choice(["low", "normal", "high"]), default=None, help="Replace the priority."
)
def item_edit(
    cli: CliContext, token: tuple[str, str], title: str | None, body_file: str | None, priority: str | None
) -> None:
    """Edit the item at TOKEN (e.g. hub:42) in place — only the given fields change."""
    source, ref = token
    json_body: dict[str, object] = {}
    if title is not None:
        json_body["title"] = title
    if body_file is not None:
        json_body["body"] = _read_body_file(body_file)
    if priority is not None:
        json_body["stated_priority"] = priority
    resp = cli.patch(
        f"/api/work-sources/{source}/items/{ref}",
        "PATCH /work-sources/{source}/items/{ref}",
        json_body=json_body,
        on_status={404: f"unknown {source}:{ref}", 409: f"work source {source!r} has no editor"},
    )
    body_json = resp.json()
    cli.show_lines(body_json, f"edited {body_json['label']}")


@item_group.command("delete", cls=FleetCommand)
@click.argument("token", type=WorkToken())
@click.option("--yes", is_flag=True, default=False, help="Skip the confirmation prompt.")
def item_delete(cli: CliContext, token: tuple[str, str], yes: bool) -> None:
    """Withdraw the item at TOKEN (e.g. hub:42)."""
    source, ref = token
    if not yes and not click.confirm(f"withdraw {source}:{ref}?"):
        raise click.Abort()
    resp = cli.delete(
        f"/api/work-sources/{source}/items/{ref}",
        "DELETE /work-sources/{source}/items/{ref}",
        on_status={404: f"unknown {source}:{ref}", 409: f"work source {source!r} has no editor"},
    )
    body_json = resp.json()
    cli.show_lines(body_json, f"withdrew {body_json['label']}")

"""``blizzard hub config`` — operator verbs over configuration: the change log, declarative documents, and the
one-shot import of a file-configured hub."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import click
import httpx

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext, RawBody
from blizzard.hub.cli.views import Listing
from blizzard.hub.documents.codec import accepted_extensions, codec_for_path

_KINDS = click.Choice(["work_source", "repository", "secret", "scope", "routine"])


class ChangeListing(Listing):
    empty = "no configuration changes yet"

    def line(self, row: Any) -> str:
        fields = ",".join(d["field"] for d in row["diff"])
        tail = f"  {fields}" if fields else ""
        return (
            f"#{row['id']}  {row['recorded_at']}  {row['actor']}  {row['door']}  "
            f"{row['record_kind']} {row['record_key']}  r{row['revision']}  {row['op']}{tail}"
        )


class ApplyListing(Listing):
    empty = "nothing named in the document"

    def line(self, row: Any) -> str:
        fields = ",".join(d["field"] for d in row["diff"])
        tail = f"  {fields}" if fields else ""
        return f"{row['op']:<9}  {row['kind']} {row['key']}{tail}"


#: The statuses the hub refuses a document with — each names the entry it stopped at.
_REFUSED = (409, 415, 422)


def _refusal(cli: CliContext, resp: httpx.Response) -> str:
    """The hub's refusal, each validation entry located by its path inside the document
    (``work_sources.1.provider: …``)."""
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, list):
        lines = []
        for entry in detail:
            where = ".".join(str(part) for part in entry.get("loc", [])[1:])
            at = f" (line {entry['line']}, column {entry['column']})" if entry.get("line") is not None else ""
            lines.append(f"{where}: {entry['msg']}{at}" if where else f"{entry['msg']}{at}")
        return "; ".join(lines)
    return cli.detail(resp, f"refused with {resp.status_code}")


@click.group("config")
def config_group() -> None:
    """Operator verbs over configuration: changes, apply, export, import-legacy."""


@config_group.command("changes", cls=FleetCommand)
@click.option("--kind", "kind", type=_KINDS, default=None, help="Only changes to this kind of record.")
@click.option("--key", "key", default=None, help="Only changes to the record with this name.")
@click.option(
    "--all", "everything", is_flag=True, default=False, help="Print every change rather than the newest page."
)
def config_changes(cli: CliContext, kind: str | None, key: str | None, everything: bool) -> None:
    """List configuration changes newest first — who changed which record, through which door, and what.

    Shows the newest page unless --all. A secret's value never appears."""
    params = {k: v for k, v in (("record_kind", kind), ("record_key", key)) if v is not None}
    rows: list[Any] = []
    while True:
        body = cli.get("/api/config/changes", "GET /config/changes", params=params).json()
        rows.extend(body["changes"])
        if not everything or body["next_before"] is None:
            break
        params["before"] = str(body["next_before"])
    cli.show({"changes": rows}, ChangeListing(rows))


@config_group.command("apply", cls=FleetCommand)
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Show what would change and write nothing.")
def config_apply(cli: CliContext, file: Path, dry_run: bool) -> None:
    """Apply the declarative document FILE (.yaml, .yml or .json) in one transaction.

    Creates what is absent, edits only the fields the document states, enables what is retired, and never
    touches a record it leaves out. Any refusal leaves everything unchanged; --dry-run prints the same outcome."""
    codec = codec_for_path(file)
    if codec is None:
        raise click.UsageError(f"{file.name}: unknown extension; use one of {', '.join(accepted_extensions())}")
    body = RawBody(content=file.read_bytes(), media_type=codec.media_types[0])
    resp = cli.send(
        "post",
        "/api/config/apply",
        raw_body=body,
        params={"dry_run": "true"} if dry_run else None,
    )
    if resp.status_code in _REFUSED:
        raise click.ClickException(_refusal(cli, resp))
    cli.check(resp, "POST /config/apply")
    payload = resp.json()
    cli.show(payload, ApplyListing(payload["outcomes"]))


@config_group.command("export", cls=FleetCommand)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["yaml", "json"]),
    default="yaml",
    show_default=True,
    help="The document format to print.",
)
def config_export(cli: CliContext, fmt: str) -> None:
    """Print every active work source, repository and secret name as a document that applies back unchanged.

    Retired records are left out, since applying one would enable it."""
    codec = codec_for_path(f"export.{fmt}")
    assert codec is not None
    document = cli.get("/api/config/export", "GET /config/export").json()
    click.echo(codec.encode(document).decode("utf-8"), nl=False)


@config_group.command("import-legacy")
@click.option(
    "--dir",
    "directory",
    default=".",
    envvar="BZ_HUB_DIR",
    help="Hub runtime directory (overrides $BZ_HUB_DIR).",
)
def config_import_legacy(directory: str) -> None:
    """Carry this hub's [[work_source]] blocks and BZ_FORGE_* variables into records — offline, on the hub host.

    Runs once, in one transaction, against a migrated store and an existing hub key. A record the store
    already holds is skipped, never overwritten; any refusal writes nothing. A second run writes nothing."""
    from blizzard.hub.app import import_legacy_config
    from blizzard.hub.config import ConfigError, HubConfig
    from blizzard.hub.domain.config.carry_over import ImportStatus, LegacyImportRefused

    try:
        result = import_legacy_config(HubConfig.load(Path(directory)), os.environ)
    except (ConfigError, LegacyImportRefused) as exc:
        raise click.ClickException(str(exc)) from exc
    if result.status is ImportStatus.ALREADY_IMPORTED:
        click.echo("the legacy configuration was already imported; nothing written")
        return
    if result.status is ImportStatus.NOTHING_TO_IMPORT:
        click.echo("no legacy configuration keys present; nothing written")
        return
    for outcome in result.outcomes:
        click.echo(f"{'created' if outcome.created else 'skipped':<8}  {outcome.kind.value} {outcome.key}")
    click.echo("imported the legacy configuration; remove its keys, then restart the hub")

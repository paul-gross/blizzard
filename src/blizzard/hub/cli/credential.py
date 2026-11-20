"""``blizzard hub credential`` — operator verbs over credential records."""

from __future__ import annotations

from typing import Any, cast

import click
from pydantic import TypeAdapter

from blizzard.cli.get_group import GetGroup
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing
from blizzard.hub.domain.config.credential_bindings import CREDENTIAL_BINDINGS
from blizzard.wire.credential import (
    CredentialCheckView,
    CredentialDocument,
    CredentialsListView,
    CredentialSummary,
    OperationResultView,
)

_SUMMARY = TypeAdapter(CredentialSummary)
_LIST = TypeAdapter(CredentialsListView)
_DOCUMENT = TypeAdapter(CredentialDocument)
_CHECK = TypeAdapter(CredentialCheckView)

_VARIANT = click.option(
    "--variant",
    type=click.Choice(["classic", "fine-grained"]),
    default=None,
    help="Which kind of GitHub token the secret holds; it names how a refused check read is reported.",
)
_APP_ID = click.option(
    "--app-id", type=int, default=None, help="The GitHub App's id, for a github.app-installation credential."
)
_INSTALLATION_ID = click.option(
    "--installation-id",
    type=int,
    default=None,
    help="The GitHub App installation's id, for a github.app-installation credential.",
)
_IF_MATCH = click.option(
    "--if-match", type=int, default=None, help="Refuse the change unless the credential is still at this revision."
)


def _state(row: CredentialSummary) -> str:
    return "retired" if row.retired else "enabled"


class CredentialListing(Listing):
    empty = "no credentials yet"

    def line(self, row: Any) -> str:
        credential = cast(CredentialSummary, row)
        return (
            f"{credential.slug}  {credential.name}  {credential.credential_id}  r{credential.revision}  "
            f"{_state(credential)}  {credential.forge_slug}  {credential.binding}  {credential.authority}"
        )


@click.group("credential", cls=GetGroup)
def credential_group() -> None:
    """Create, list, get, edit, retire and enable the credentials repositories and work sources act with."""


@credential_group.command("create", cls=FleetCommand)
@click.argument("slug")
@click.option("--name", default=None, help="The credential's display name; its slug when omitted.")
@click.option("--forge", required=True, help="The forge the credential acts on, by id or slug.")
@click.option(
    "--binding",
    required=True,
    help=f"How the credential authenticates: one of {', '.join(CREDENTIAL_BINDINGS)}.",
)
@click.option("--secret", required=True, help="The stored secret the credential reads.")
@_VARIANT
@_APP_ID
@_INSTALLATION_ID
def credential_create(
    cli: CliContext,
    slug: str,
    name: str | None,
    forge: str,
    binding: str,
    secret: str,
    variant: str | None,
    app_id: int | None,
    installation_id: int | None,
) -> None:
    """Add credential SLUG at revision 1 on the forge --forge names.

    Refused when SLUG is another credential's, when another credential on the forge already acts as the same
    authority, or when the secret is missing or retired."""
    body = {
        "slug": slug,
        **({} if name is None else {"name": name}),
        "forge": forge,
        "binding": binding,
        "secret": secret,
        "settings": _settings(variant, app_id, installation_id),
    }
    resp = cli.post("/api/credentials", "POST /credentials", json_body=body, door=True)
    raw, row = cli.decode(resp, _SUMMARY, "POST /credentials")
    cli.show_lines(raw, f"credential {row.slug} created at revision 1 — id {row.credential_id}")


@credential_group.command("list", cls=FleetCommand)
@click.option("--include-retired", is_flag=True, help="Also list retired credentials.")
def credential_list(cli: CliContext, include_retired: bool) -> None:
    """List credentials by slug with their id, revision, state, forge, binding and authority."""
    params = {"include_retired": "true"} if include_retired else None
    resp = cli.get("/api/credentials", "GET /credentials", params=params)
    raw, listed = cli.decode(resp, _LIST, "GET /credentials")
    cli.show(raw["credentials"], CredentialListing(listed.credentials))


@credential_group.command("get", cls=FleetCommand)
@click.argument("credential")
def credential_show(cli: CliContext, credential: str) -> None:
    """Show CREDENTIAL — named by its id, its slug, or a slug it has left — with its fields and every repository and
    work source naming it."""
    resp = cli.get(f"/api/credentials/{credential}", "GET /credentials/{credential}")
    raw, row = cli.decode(resp, _DOCUMENT, "GET /credentials/{credential}")
    cli.show_lines(
        raw,
        f"{row.slug}  {row.name}  revision {row.revision}  {_state(row)}",
        f"id {row.credential_id}",
        f"forge {row.forge_slug}  binding {row.binding}  secret {row.secret_name}",
        f"authority {row.authority}",
        *(f"setting {key} {value}" for key, value in sorted(row.settings.items())),
        f"created {row.created_at} by {row.created_by}",
        *(f"{r.kind} {r.label}  {r.key}" for r in row.referrers),
    )


@credential_group.command("edit", cls=FleetCommand)
@click.argument("credential")
@click.option("--slug", default=None, help="The new slug; the one it leaves keeps naming the credential.")
@click.option("--name", default=None, help="The new display name.")
@click.option("--secret", default=None, help="The new secret; every record naming the credential follows it.")
@_VARIANT
@_APP_ID
@_INSTALLATION_ID
@_IF_MATCH
def credential_edit(
    cli: CliContext,
    credential: str,
    slug: str | None,
    name: str | None,
    secret: str | None,
    variant: str | None,
    app_id: int | None,
    installation_id: int | None,
    if_match: int | None,
) -> None:
    """Change only the fields given on CREDENTIAL; a no-op keeps its revision. Any setting given replaces all its
    settings, so give every setting its binding requires.

    Refused when the new slug is another credential's, when --if-match names a stale revision, or when the change
    would take away a capability an enabled repository acting with it needs."""
    body: dict[str, Any] = {k: v for k, v in {"slug": slug, "name": name, "secret": secret}.items() if v is not None}
    settings = _settings(variant, app_id, installation_id)
    if settings:
        body["settings"] = settings
    if not body:
        raise click.UsageError("nothing to change — give a field to set")
    resp = cli.patch(
        f"/api/credentials/{credential}",
        "PATCH /credentials/{credential}",
        json_body=body,
        if_match=if_match,
        door=True,
    )
    raw, row = cli.decode(resp, _SUMMARY, "PATCH /credentials/{credential}")
    cli.show_lines(raw, f"credential {row.slug} is at revision {row.revision}")


@credential_group.command("retire", cls=FleetCommand)
@click.argument("credential")
@_IF_MATCH
def credential_retire(cli: CliContext, credential: str, if_match: int | None) -> None:
    """Retire CREDENTIAL, refusing while an enabled record names it."""
    _lifecycle(cli, credential, "retire", if_match)


@credential_group.command("enable", cls=FleetCommand)
@click.argument("credential")
@_IF_MATCH
def credential_enable(cli: CliContext, credential: str, if_match: int | None) -> None:
    """Re-enable a retired CREDENTIAL. Refused while its secret is retired."""
    _lifecycle(cli, credential, "enable", if_match)


#: Above the hub's 60-second cap on a check, so the hub answers the uses it did not reach before the client gives up.
_CHECK_TIMEOUT = 75.0

#: An outcome that fails the check's exit status.
_FAILING = frozenset({"refused", "unsupported"})


@credential_group.command("check", cls=FleetCommand)
@click.argument("credential")
@click.option("--repo", "repositories", multiple=True, help="Check only this repository, by id or slug; repeatable.")
@click.option("--source", "work_sources", multiple=True, help="Check only this work source, by id or slug; repeatable.")
def credential_check(
    cli: CliContext, credential: str, repositories: tuple[str, ...], work_sources: tuple[str, ...]
) -> None:
    """Check CREDENTIAL read-only against the repositories and work sources it acts for — those given by --repo and
    --source, else every enabled one naming it — reporting each operation proven, granted, unproven, refused or
    unsupported. Nothing is written to the forge or stored.

    Exits 1 when any operation is refused or unsupported, or when the check reached none of its uses, else 0. Refused
    when a --repo or --source does not act with CREDENTIAL, or when its secret is missing or retired."""
    body: dict[str, list[str]] = {}
    if repositories:
        body["repositories"] = list(repositories)
    if work_sources:
        body["work_sources"] = list(work_sources)
    resp = cli.post(
        f"/api/credentials/{credential}/checks",
        "POST /credentials/{credential}/checks",
        json_body=body,
        timeout=_CHECK_TIMEOUT,
    )
    raw, check = cli.decode(resp, _CHECK, "POST /credentials/{credential}/checks")
    cli.show_lines(raw, *_check_lines(check))
    if _failed(check):
        raise SystemExit(1)


def _check_lines(check: CredentialCheckView) -> list[str]:
    lines = [f"credential {check.credential}  {check.binding}" + (f"  {check.variant}" if check.variant else "")]
    if check.variant_contradicted:
        lines.append("warning: the declared variant contradicts the token the secret holds")
    if check.mint is not None:
        lines.append(_operation_line(check.mint))
    for use in check.uses:
        reached = "" if use.reached else "  (not reached)"
        lines.append(f"{use.kind} {use.slug}  {use.id}{reached}")
        lines.extend(f"  {_operation_line(operation)}" for operation in use.operations)
    if check.unreached_reason is not None:
        lines.append(f"no use reached: {check.unreached_reason}")
    lines.append(check.not_evaluated)
    return lines


def _operation_line(operation: OperationResultView) -> str:
    line = f"{operation.operation}  {operation.outcome}"
    return f"{line}  {operation.detail}" if operation.detail else line


def _failed(check: CredentialCheckView) -> bool:
    mint = [check.mint] if check.mint is not None else []
    operations = [*mint, *(operation for use in check.uses for operation in use.operations)]
    unreached = bool(check.uses) and not any(use.reached for use in check.uses)
    return unreached or any(str(operation.outcome) in _FAILING for operation in operations)


def _settings(variant: str | None, app_id: int | None, installation_id: int | None) -> dict[str, object]:
    given = {"variant": variant, "app_id": app_id, "installation_id": installation_id}
    return {key: value for key, value in given.items() if value is not None}


def _lifecycle(cli: CliContext, credential: str, verb: str, if_match: int | None) -> None:
    operation = f"POST /credentials/{{credential}}/{verb}"
    resp = cli.post(f"/api/credentials/{credential}/{verb}", operation, if_match=if_match, door=True)
    raw, row = cli.decode(resp, _SUMMARY, operation)
    cli.show_lines(raw, f"credential {row.slug} is now {_state(row)} at revision {row.revision}")

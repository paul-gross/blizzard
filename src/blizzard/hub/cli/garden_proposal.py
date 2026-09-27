"""``blizzard hub garden-proposal`` — read verbs over garden proposals,
two closing verbs (``pass``/``accept``), and four
operator-authoring verbs (``create``/``edit``/``attach``/``detach``)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.inputs import read_body_file
from blizzard.hub.cli.views import Listing


class GardenProposalListing(Listing):
    empty = "no garden proposals"

    def line(self, row: Any) -> str:
        origin = row["origin"]
        who = f"created_by={row['created_by']}" if origin == "operator" else f"routine={row['routine_name']}"
        return f"{row['proposal_id']}  class={row['class']}  origin={origin}  {who}  {row['title']}"


def _closure_lines(closure: dict[str, Any] | None) -> Iterator[str]:
    if closure is None:
        return
    if closure["closure"] == "passed":
        yield f"  passed by {closure['closed_by']} at {closure['closed_at']}: {closure['reason']}"
        return
    if closure["item_outcome"] == "minted":
        yield f"  accepted by {closure['closed_by']} at {closure['closed_at']} → {closure['source']}:{closure['ref']}"
    else:
        yield f"  accepted by {closure['closed_by']} at {closure['closed_at']}, no work item minted"
    if closure["reason"]:
        yield f"  reason: {closure['reason']}"


@dataclass(frozen=True)
class GardenProposalDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        origin = body["origin"]
        yield f"{body['proposal_id']}  origin={origin}  routine={body['routine_name']}  class={body['class']}"
        if origin == "operator":
            yield f"  created_by={body['created_by']}"
        yield f"  {body['title']}"
        yield f"  {body['body']}"
        if body["findings"]:
            yield f"  findings: {', '.join(body['findings'])}"
        yield from _closure_lines(body.get("closure"))
        chunk_id = body.get("chunk_id")  # the accept response only
        if chunk_id is not None:
            yield f"  → chunk {chunk_id}"


@click.group("garden-proposal")
def garden_proposal_group() -> None:
    """List, inspect, pass, or accept a garden proposal."""


@garden_proposal_group.command("list", cls=FleetCommand)
@click.option("--origin", type=click.Choice(["routine-run", "operator"]), default=None, help="Narrow to one origin.")
def garden_proposal_list(cli: CliContext, origin: str | None) -> None:
    """List every garden proposal, newest first."""
    params = {"origin": origin} if origin is not None else None
    rows = cli.get_all("/api/garden-proposals", "GET /garden-proposals", key="proposals", params=params)
    cli.show(rows, GardenProposalListing(rows))


@garden_proposal_group.command("show", cls=FleetCommand)
@click.argument("proposal_id")
def garden_proposal_show(cli: CliContext, proposal_id: str) -> None:
    """One garden proposal's whole record."""
    resp = cli.get(
        f"/api/garden-proposals/{proposal_id}",
        "GET /garden-proposals/{id}",
        on_status={404: f"unknown garden proposal {proposal_id}"},
    )
    body = resp.json()
    cli.show(body, GardenProposalDetail(body))


def _already_closed_fallback(proposal_id: str) -> str:
    return f"garden proposal {proposal_id} already carries a closure"


@garden_proposal_group.command("pass", cls=FleetCommand)
@click.argument("proposal_id")
@click.option("--reason", required=True, help="Why the proposal is passed.")
def garden_proposal_pass(cli: CliContext, proposal_id: str, reason: str) -> None:
    """Pass PROPOSAL_ID, recording REASON.

    Passing is not a dismissal — it is the note that stops a later run raising the same
    response as though it were new."""
    resp = cli.post(
        f"/api/garden-proposals/{proposal_id}/pass",
        "POST /garden-proposals/{id}/pass",
        json_body={"reason": reason},
        on_status={
            404: f"unknown garden proposal {proposal_id}",
            409: _already_closed_fallback(proposal_id),
            422: "passing a garden proposal requires a reason",
        },
    )
    body = resp.json()
    cli.show(body, GardenProposalDetail(body))


@garden_proposal_group.command("accept", cls=FleetCommand)
@click.argument("proposal_id")
@click.option("--reason", default=None, help="Why the proposal is accepted.")
@click.option(
    "--body-file",
    "body_file",
    default=None,
    help=(
        "Replace the proposal's own body, from a path or '-' for stdin, as the prose the minted "
        "item's 'Related findings' template wraps (default: the proposal's own body)."
    ),
)
@click.option("--no-work-item", "no_work_item", is_flag=True, default=False, help="Decline to mint a linked work item.")
def garden_proposal_accept(
    cli: CliContext, proposal_id: str, reason: str | None, body_file: str | None, no_work_item: bool
) -> None:
    """Accept PROPOSAL_ID.

    Mints a linked hub work item by default, wrapping the proposal's own body unless
    --body-file supplies another in the "Related findings" template; --no-work-item
    declines to mint, and the decline is recorded rather than left to read as an absent
    link."""
    json_body: dict[str, object] = {"mint_work_item": not no_work_item}
    if reason is not None:
        json_body["reason"] = reason
    if body_file is not None:
        json_body["body"] = read_body_file(body_file)
    resp = cli.post(
        f"/api/garden-proposals/{proposal_id}/accept",
        "POST /garden-proposals/{id}/accept",
        json_body=json_body,
        on_status={404: f"unknown garden proposal {proposal_id}", 409: _already_closed_fallback(proposal_id)},
    )
    body = resp.json()
    cli.show(body, GardenProposalDetail(body))


@garden_proposal_group.command("create", cls=FleetCommand)
@click.option("--title", required=True, help="The proposal's title.")
@click.option("--class", "class_", required=True, help="The deployment's own taxonomy class.")
@click.option("--body-file", "body_file", required=True, help="Path to the proposal's body, or '-' for stdin.")
@click.option("--routine", default=None, help="An existing routine to name (optional).")
@click.option("--finding", "findings", multiple=True, help="A finding id to link (repeatable).")
def garden_proposal_create(
    cli: CliContext, title: str, class_: str, body_file: str, routine: str | None, findings: tuple[str, ...]
) -> None:
    """Author a fresh operator garden proposal, naming zero or more findings.

    --body-file may be '-' to read the body from stdin. Carries no scope: a linked
    finding keeps its own."""
    body = read_body_file(body_file)
    json_body: dict[str, object] = {"title": title, "class": class_, "body": body, "findings": list(findings)}
    if routine is not None:
        json_body["routine"] = routine
    resp = cli.post("/api/garden-proposals", "POST /garden-proposals", json_body=json_body)
    body_json = resp.json()
    cli.show(body_json, GardenProposalDetail(body_json))


@garden_proposal_group.command("edit", cls=FleetCommand)
@click.argument("proposal_id")
@click.option("--title", default=None, help="Replace the title.")
@click.option("--class", "class_", default=None, help="Replace the class.")
@click.option("--body-file", "body_file", default=None, help="Replace the body from a path, or '-' for stdin.")
def garden_proposal_edit(
    cli: CliContext, proposal_id: str, title: str | None, class_: str | None, body_file: str | None
) -> None:
    """Edit PROPOSAL_ID in place — only the given fields change. Works on either origin
    while open; refused with 409 once it carries a closure."""
    json_body: dict[str, object] = {}
    if title is not None:
        json_body["title"] = title
    if class_ is not None:
        json_body["class"] = class_
    if body_file is not None:
        json_body["body"] = read_body_file(body_file)
    resp = cli.patch(
        f"/api/garden-proposals/{proposal_id}",
        "PATCH /garden-proposals/{id}",
        json_body=json_body,
        on_status={404: f"unknown garden proposal {proposal_id}", 409: _already_closed_fallback(proposal_id)},
    )
    body_json = resp.json()
    cli.show(body_json, GardenProposalDetail(body_json))


@garden_proposal_group.command("attach", cls=FleetCommand)
@click.argument("proposal_id")
@click.argument("finding_ids", nargs=-1, required=True)
def garden_proposal_attach(cli: CliContext, proposal_id: str, finding_ids: tuple[str, ...]) -> None:
    """Link FINDING_IDS to PROPOSAL_ID. Works on either origin while open; refused with
    409 once it carries a closure."""
    resp = cli.post(
        f"/api/garden-proposals/{proposal_id}/attach",
        "POST /garden-proposals/{id}/attach",
        json_body={"findings": list(finding_ids)},
        on_status={404: f"unknown garden proposal {proposal_id}", 409: _already_closed_fallback(proposal_id)},
    )
    body_json = resp.json()
    cli.show(body_json, GardenProposalDetail(body_json))


@garden_proposal_group.command("detach", cls=FleetCommand)
@click.argument("proposal_id")
@click.argument("finding_ids", nargs=-1, required=True)
def garden_proposal_detach(cli: CliContext, proposal_id: str, finding_ids: tuple[str, ...]) -> None:
    """Unlink FINDING_IDS from PROPOSAL_ID. Works on either origin while open; refused
    with 409 once it carries a closure."""
    resp = cli.post(
        f"/api/garden-proposals/{proposal_id}/detach",
        "POST /garden-proposals/{id}/detach",
        json_body={"findings": list(finding_ids)},
        on_status={404: f"unknown garden proposal {proposal_id}", 409: _already_closed_fallback(proposal_id)},
    )
    body_json = resp.json()
    cli.show(body_json, GardenProposalDetail(body_json))

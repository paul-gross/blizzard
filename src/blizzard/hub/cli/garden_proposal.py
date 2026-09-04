"""``blizzard hub garden-proposal`` — blizzard#390: read verbs over garden proposals,
plus blizzard#395's two closing verbs, ``pass`` and ``accept``."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing


class GardenProposalListing(Listing):
    empty = "no garden proposals"

    def line(self, row: Any) -> str:
        return f"{row['proposal_id']}  class={row['class']}  {row['title']}"


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
        yield f"{body['proposal_id']}  routine={body['routine_name']}  class={body['class']}"
        yield f"  {body['title']}"
        yield f"  {body['body']}"
        yield f"  findings: {', '.join(body['findings'])}"
        yield from _closure_lines(body.get("closure"))
        chunk_id = body.get("chunk_id")  # the accept response only
        if chunk_id is not None:
            yield f"  → chunk {chunk_id}"


@click.group("garden-proposal")
def garden_proposal_group() -> None:
    """List, inspect, pass, or accept a garden proposal."""


@garden_proposal_group.command("list", cls=FleetCommand)
def garden_proposal_list(cli: CliContext) -> None:
    """List every garden proposal, newest first."""
    rows = cli.get("/api/garden-proposals", "GET /garden-proposals").json()
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


def _read_body_file(path: str) -> str:
    """PATH's contents, or stdin when PATH is ``-`` (``item create`` precedent)."""
    if path == "-":
        return click.get_text_stream("stdin").read()
    try:
        return Path(path).read_text()
    except OSError as exc:
        raise click.ClickException(f"failed to read {path}: {exc}") from exc


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
        json_body["body"] = _read_body_file(body_file)
    resp = cli.post(
        f"/api/garden-proposals/{proposal_id}/accept",
        "POST /garden-proposals/{id}/accept",
        json_body=json_body,
        on_status={404: f"unknown garden proposal {proposal_id}", 409: _already_closed_fallback(proposal_id)},
    )
    body = resp.json()
    cli.show(body, GardenProposalDetail(body))

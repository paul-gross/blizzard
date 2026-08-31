"""``blizzard hub finding`` — blizzard#390: read verbs over findings; blizzard#394 Phase 2
adds the human-driven exit verbs and `reopen`."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing


class FindingListing(Listing):
    empty = "no findings"

    def line(self, row: Any) -> str:
        marker = row["state"]
        suffix = f"  ({row['note']})" if row.get("note") else ""
        return f"{row['finding_id']}  {marker}  class={row['class']}  {row['locus']}{suffix}"


@dataclass(frozen=True)
class FindingDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        yield f"{body['finding_id']}  {body['state']}  routine={body['routine_name']}  scope={body['scope_slug']}"
        yield f"  class={body['class']}  locus={body['locus']}"
        yield f"  {body['summary']}"
        if body.get("introduced"):
            yield f"  introduced: {body['introduced']}"
        yield f"  last_seen_at={body.get('last_seen_at') or '-'}  observed_count={body['observed_count']}"
        if body.get("note"):
            yield f"  note: {body['note']}"


def _blank_note_fallback(verb: str) -> str:
    return f"{verb!r} requires a non-empty note"


@click.group("finding")
def finding_group() -> None:
    """List or inspect findings, or exit/reopen one or many."""


@finding_group.command("list", cls=FleetCommand)
@click.option("--routine", "routine", required=True, help="The routine whose findings to list.")
@click.option("--scope", "scope", required=True, help="The scope to filter to.")
@click.option("--include-gone", is_flag=True, default=False, help="Also show findings whose newest fact is gone (D3).")
def finding_list(cli: CliContext, routine: str, scope: str, include_gone: bool) -> None:
    """List ROUTINE's findings under SCOPE — live only, unless --include-gone.

    This is the read a running pass calls to cross-reference its own bucket
    (blizzard-context:/domain/findings-and-proposals.md)."""
    rows = cli.get(
        "/api/findings",
        "GET /findings",
        params={"routine": routine, "scope": scope, "include_gone": str(include_gone).lower()},
    ).json()
    cli.show(rows, FindingListing(rows))


@finding_group.command("show", cls=FleetCommand)
@click.argument("finding_id")
def finding_show(cli: CliContext, finding_id: str) -> None:
    """One finding's whole record."""
    resp = cli.get(
        f"/api/findings/{finding_id}", "GET /findings/{id}", on_status={404: f"unknown finding {finding_id}"}
    )
    body = resp.json()
    cli.show(body, FindingDetail(body))


def _post_exit(
    cli: CliContext, *, path: str, wire_name: str, finding_ids: tuple[str, ...], note: str, verb: str, **extra: str
) -> None:
    rows = cli.post(
        f"/api/findings/{path}",
        wire_name,
        json_body={"finding_ids": list(finding_ids), "note": note, **extra},
        on_status={
            404: f"unknown finding among {', '.join(finding_ids)}",
            422: _blank_note_fallback(verb),
        },
    ).json()
    cli.show(rows, FindingListing(rows))


@finding_group.command("resolve", cls=FleetCommand)
@click.argument("finding_ids", nargs=-1, required=True)
@click.option("--note", required=True, help="Why the finding is resolved — the work that answered it.")
def finding_resolve(cli: CliContext, finding_ids: tuple[str, ...], note: str) -> None:
    """Resolve FINDING_IDS: the work that answers them has landed."""
    _post_exit(
        cli, path="resolve", wire_name="POST /findings/resolve", finding_ids=finding_ids, note=note, verb="resolve"
    )


@finding_group.command("confirm-gone", cls=FleetCommand)
@click.argument("finding_ids", nargs=-1, required=True)
@click.option("--note", required=True, help="How it was confirmed the finding no longer reproduces.")
def finding_confirm_gone(cli: CliContext, finding_ids: tuple[str, ...], note: str) -> None:
    """Confirm by hand that FINDING_IDS no longer reproduce."""
    _post_exit(
        cli,
        path="confirm-gone",
        wire_name="POST /findings/confirm-gone",
        finding_ids=finding_ids,
        note=note,
        verb="confirm-gone",
    )


@finding_group.command("wont-fix", cls=FleetCommand)
@click.argument("finding_ids", nargs=-1, required=True)
@click.option("--note", required=True, help="Why FINDING_IDS won't be fixed.")
def finding_wont_fix(cli: CliContext, finding_ids: tuple[str, ...], note: str) -> None:
    """Withdraw FINDING_IDS as won't-fix — the ground hasn't moved, they don't merit standing."""
    _post_exit(
        cli, path="wont-fix", wire_name="POST /findings/wont-fix", finding_ids=finding_ids, note=note, verb="wont-fix"
    )


@finding_group.command("not-a-finding", cls=FleetCommand)
@click.argument("finding_ids", nargs=-1, required=True)
@click.option("--note", required=True, help="Why FINDING_IDS are not findings.")
def finding_not_a_finding(cli: CliContext, finding_ids: tuple[str, ...], note: str) -> None:
    """Withdraw FINDING_IDS as not findings."""
    _post_exit(
        cli,
        path="not-a-finding",
        wire_name="POST /findings/not-a-finding",
        finding_ids=finding_ids,
        note=note,
        verb="not-a-finding",
    )


@finding_group.command("supersede", cls=FleetCommand)
@click.argument("finding_ids", nargs=-1, required=True)
@click.option("--by", "superseded_by", required=True, help="The absorbing finding's id.")
@click.option("--note", required=True, help="How FINDING_IDS fold into --by.")
def finding_supersede(cli: CliContext, finding_ids: tuple[str, ...], superseded_by: str, note: str) -> None:
    """Withdraw FINDING_IDS as superseded by --by."""
    _post_exit(
        cli,
        path="supersede",
        wire_name="POST /findings/supersede",
        finding_ids=finding_ids,
        note=note,
        verb="supersede",
        superseded_by=superseded_by,
    )


@finding_group.command("reopen", cls=FleetCommand)
@click.argument("finding_ids", nargs=-1, required=True)
@click.option("--note", required=True, help="Why FINDING_IDS are reopened.")
def finding_reopen(cli: CliContext, finding_ids: tuple[str, ...], note: str) -> None:
    """Reopen FINDING_IDS, undoing whichever exit or gone fact was newest."""
    _post_exit(cli, path="reopen", wire_name="POST /findings/reopen", finding_ids=finding_ids, note=note, verb="reopen")

"""``blizzard hub run`` — reading routine runs: the windowed list and one run's own
delta. Distinct from ``blizzard hub routine run``, which starts a new run rather than
reading an existing one."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import click
import httpx

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing


def _utc_query_value(value: datetime | None) -> str | None:
    """A bare ``--since``/``--until`` is read as the operator's own local wall clock,
    not UTC — converted before it crosses the wire (``routine trend``'s own rule)."""
    return iso_utc(value.astimezone(UTC)) if value is not None else None


class RunListing(Listing):
    empty = "no runs in this window"

    def line(self, row: Any) -> str:
        delivered = ", ".join(f"{d['finding_set_id']}" for d in row["delivered"]) or "-"
        return (
            f"{row['chunk_id']}  {row['minted_at']}  {row['routine_name']}/{row['scope_slug']}  "
            f"mode={row['mode']}  outcome={row['outcome']}  delivered=[{delivered}]"
        )


@click.group("run")
def run_group() -> None:
    """Reading routine runs: the windowed list and one run's own delta."""


@run_group.command("list", cls=FleetCommand)
@click.option("--since", default=None, type=click.DateTime(), help="The window's start, in local time.")
@click.option("--until", default=None, type=click.DateTime(), help="The window's end, in local time (exclusive).")
def run_list(cli: CliContext, since: datetime | None, until: datetime | None) -> None:
    """List every run minted in --since/--until, newest first — both default to the
    last 24 hours ending now."""
    params = {
        k: v for k, v in {"since": _utc_query_value(since), "until": _utc_query_value(until)}.items() if v is not None
    }
    resp = cli.send("get", "/api/runs", params=params)
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        raise click.ClickException(f"run list rejected: {cli.detail(resp, 'validation failed')}")
    cli.check(resp, "GET /runs")
    rows = resp.json()
    cli.show(rows, RunListing(rows))


@dataclass(frozen=True)
class RunDetail:
    """``run show``'s own render — identity and outcome, the open escalation when the
    run needs a human, then each delivered set's own added/observed/gone groups."""

    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        yield f"{body['chunk_id']}  {body['routine_name']}/{body['scope_slug']}  mode={body['mode']}"
        yield f"  outcome: {body['outcome']}"
        escalation = body["escalation"]
        if escalation is not None:
            yield f"  escalated at: {escalation['node_name'] or 'unknown node'}"
            yield f"    takeover: {escalation['takeover_command']}"
            if escalation["wrapped_takeover_command"]:
                yield f"    takeover (wrapped): {escalation['wrapped_takeover_command']}"
        for delivered in body["sets"]:
            revisions = ", ".join(f"{repo}@{rev}" for repo, rev in sorted(delivered["revisions"].items()))
            yield f"  set {delivered['finding_set_id']}  ({revisions or 'no repositories recorded'})"
            if delivered["measurement"] is not None:
                yield f"    measurement: {delivered['measurement']}"
            for added in delivered["added"]:
                finding_id = added["finding_id"] or "unmatched"
                yield f"    + [{finding_id}] {added['class']}  {added['locus']}: {added['summary']}"
            for observed in delivered["observed"]:
                # An observed id naming no finding row carries none of the three
                # descriptive fields — it still renders, by id alone.
                described = (
                    f" {observed['class']}  {observed['locus']}: {observed['summary']}" if observed["class"] else ""
                )
                yield f"    = {observed['finding_id']}{described}"
            for gone in delivered["gone"]:
                yield f"    - {gone['finding_id']}: {gone['note']}"


@run_group.command("show", cls=FleetCommand)
@click.argument("chunk_id")
def run_show(cli: CliContext, chunk_id: str) -> None:
    """One run's full detail — identity, derived outcome, and, per finding set it
    delivered, the added/observed/gone entries its own artifact published."""
    resp = cli.get(f"/api/runs/{chunk_id}", "GET /runs/{id}", on_status={404: f"unknown run {chunk_id}"})
    body = resp.json()
    cli.show(body, RunDetail(body))

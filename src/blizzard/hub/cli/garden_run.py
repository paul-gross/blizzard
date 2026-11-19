"""``blizzard hub run`` — reading routine runs: the windowed list and one run's own
delta. Distinct from ``blizzard hub routine run``."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import click
from pydantic import TypeAdapter

from blizzard.cli.get_group import GetGroup
from blizzard.cli.window import since_option, until_option, utc_query_value
from blizzard.foundation.roles import dto
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.project_option import project_option, project_query
from blizzard.hub.cli.views import Listing
from blizzard.wire.garden_run import RunDeltaView, RunRowView

_RUN_ROWS = TypeAdapter(list[RunRowView])
_RUN_DELTA = TypeAdapter(RunDeltaView)


class RunListing(Listing):
    empty = "no runs in this window"

    def line(self, row: Any) -> str:
        run: RunRowView = row
        delivered = ", ".join(d.finding_set_id for d in run.delivered) or "-"
        return (
            f"{run.chunk_id}  {run.minted_at}  {run.routine_name}/{run.scope_slug}  "
            f"mode={run.mode}  outcome={run.outcome}  delivered=[{delivered}]"
        )


@click.group("run", cls=GetGroup)
def run_group() -> None:
    """Read routine runs and what each one changed."""


@run_group.command("list", cls=FleetCommand)
@since_option()
@until_option()
@project_option(required_by_hub=False)
def run_list(cli: CliContext, since: datetime | None, until: datetime | None, project: str | None) -> None:
    """List every run minted in --since/--until, newest first — both default to the
    last 24 hours ending now."""
    params = {
        k: v for k, v in {"since": utc_query_value(since), "until": utc_query_value(until)}.items() if v is not None
    }
    resp = cli.send("get", "/api/runs", params=params | project_query(project))
    cli.check(resp, "GET /runs")
    raw, rows = cli.decode(resp, _RUN_ROWS, "GET /runs")
    cli.show(raw, RunListing(rows))


@dto
@dataclass(frozen=True)
class RunDetail:
    """``run get``'s own render — identity and outcome, the open escalation when the
    run needs a human, then each delivered set's own added/observed/gone groups."""

    body: RunDeltaView

    def lines(self) -> Iterator[str]:
        body = self.body
        yield f"{body.chunk_id}  {body.routine_name}/{body.scope_slug}  mode={body.mode}"
        yield f"  outcome: {body.outcome}"
        escalation = body.escalation
        if escalation is not None:
            yield f"  escalated at: {escalation.node_name or 'unknown node'}"
            yield f"    takeover: {escalation.takeover_command}"
            if escalation.wrapped_takeover_command:
                yield f"    takeover (wrapped): {escalation.wrapped_takeover_command}"
        for delivered in body.sets:
            revisions = ", ".join(f"{repo}@{rev}" for repo, rev in sorted(delivered.revisions.items()))
            yield f"  set {delivered.finding_set_id}  ({revisions or 'no repositories recorded'})"
            if delivered.measurement is not None:
                yield f"    measurement: {delivered.measurement}"
            for added in delivered.added:
                finding_id = added.finding_id or "unmatched"
                yield f"    + [{finding_id}] {added.class_}  {added.locus}: {added.summary}"
            for observed in delivered.observed:
                # An observed id naming no finding row carries none of the three
                # descriptive fields — it still renders, by id alone.
                described = f" {observed.class_}  {observed.locus}: {observed.summary}" if observed.class_ else ""
                yield f"    = {observed.finding_id}{described}"
            for gone in delivered.gone:
                yield f"    - {gone.finding_id}: {gone.note}"


@run_group.command("get", cls=FleetCommand)
@click.argument("chunk_id")
def run_show(cli: CliContext, chunk_id: str) -> None:
    """One run's full detail — identity, derived outcome, and, per finding set it
    delivered, the added/observed/gone entries its own artifact published."""
    resp = cli.get(f"/api/runs/{chunk_id}", "GET /runs/{id}")
    raw, body = cli.decode(resp, _RUN_DELTA, "GET /runs/{id}")
    cli.show(raw, RunDetail(body))

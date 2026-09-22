"""``blizzard hub status`` — the fleet view: every chunk, the runners, and open questions."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import ChunkRow, Cost, CostEstimate, QuestionRow, RunnerRow

# The since-the-beginning-of-time cutoff `hub status` passes ``GET /api/spend`` (issue #60).
_FLEET_SPEND_SINCE = "1970-01-01T00:00:00+00:00"


@dataclass(frozen=True)
class FleetStatus:
    chunks: list[dict[str, Any]]
    runners: list[dict[str, Any]]
    questions: list[dict[str, Any]]
    spend: dict[str, Any]

    def lines(self) -> Iterator[str]:
        yield f"chunks ({len(self.chunks)}):"
        for chunk in self.chunks:
            yield f"  {ChunkRow(chunk, prefer_node_name=False).line()}"
        yield f"\nrunners ({len(self.runners)}):"
        for runner in self.runners:
            yield f"  {RunnerRow(runner).line()}"
        yield f"\nopen questions ({len(self.questions)}):"
        for question in self.questions:
            yield f"  {QuestionRow(question).line()}"
        billed = Cost(self.spend["cost_usd"], self.spend["cost_partial"]).rendered
        yield f"\nfleet spend (all time): {billed}{CostEstimate.suffix(self.spend)}"


@click.command(cls=FleetCommand)
def status(cli: CliContext) -> None:
    """The fleet view: every chunk with its derived status, the runners, and open questions."""
    chunks = cli.get_all("/api/chunks", "GET /chunks", key="chunks")
    runners = cli.get("/api/runners", "GET /runners")
    questions = cli.get("/api/questions", "GET /questions")
    spend = cli.get("/api/spend", "GET /spend", params={"since": _FLEET_SPEND_SINCE})

    view = FleetStatus(
        chunks=chunks,
        runners=runners.json().get("runners", []),
        questions=questions.json(),
        spend=spend.json(),
    )
    cli.show(
        {"chunks": view.chunks, "runners": runners.json(), "questions": view.questions, "spend": view.spend},
        view,
    )

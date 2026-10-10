"""``blizzard hub status`` — the fleet view: every chunk, the runners, and open questions."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import click

from blizzard.foundation.roles import dto
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.project_option import project_query
from blizzard.hub.cli.views import ChunkLine, Cost, QuestionLine, RunnerLine

# The since-the-beginning-of-time cutoff `hub status` passes ``GET /api/spend``.
_FLEET_SPEND_SINCE = "1970-01-01T00:00:00+00:00"


@dto
@dataclass(frozen=True)
class FleetStatus:
    chunks: list[dict[str, Any]]
    runners: list[dict[str, Any]]
    questions: list[dict[str, Any]]
    spend: dict[str, Any]

    def lines(self) -> Iterator[str]:
        yield f"chunks ({len(self.chunks)}):"
        for chunk in self.chunks:
            yield f"  {ChunkLine(chunk, prefer_node_name=False).line()}"
        yield f"\nrunners ({len(self.runners)}):"
        for runner in self.runners:
            yield f"  {RunnerLine(runner).line()}"
        yield f"\nopen questions ({len(self.questions)}):"
        for question in self.questions:
            yield f"  {QuestionLine(question).line()}"
        yield f"\nfleet spend (all time): {Cost.of(self.spend).rendered}"


@click.command(cls=FleetCommand)
@click.option(
    "--project",
    "project",
    default=None,
    metavar="PROJECT",
    help="Show only this project's chunks, their open questions and its spend, by id or slug; the runners are "
    "the whole fleet. Without it, every project of the tenant is shown.",
)
def status(cli: CliContext, project: str | None) -> None:
    """The fleet view: every chunk with its derived status, the runners, and open questions."""
    lens = project_query(project)
    chunks = cli.get_all("/api/chunks", "GET /chunks", key="chunks", params=lens)
    runners = cli.get("/api/runners", "GET /runners")
    questions = cli.get("/api/questions", "GET /questions").json()
    if project is not None:
        shown = {chunk["chunk_id"] for chunk in chunks}
        questions = [question for question in questions if question.get("chunk_id") in shown]
    spend = cli.get("/api/spend", "GET /spend", params={"since": _FLEET_SPEND_SINCE} | lens)

    view = FleetStatus(
        chunks=chunks,
        runners=runners.json().get("runners", []),
        questions=questions,
        spend=spend.json(),
    )
    cli.show(
        {"chunks": view.chunks, "runners": runners.json(), "questions": view.questions, "spend": view.spend},
        view,
    )

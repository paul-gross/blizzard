"""``blizzard hub status`` — the fleet view: every chunk, the runners, and open questions."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

import click
from pydantic import TypeAdapter

from blizzard.foundation.roles import dto
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.pages import read_pages
from blizzard.hub.cli.project_option import project_query
from blizzard.hub.cli.views import ChunkLine, Cost, QuestionLine, RunnerLine, RunnerRow
from blizzard.wire.chunk import ChunksPageView, ChunkSummary
from blizzard.wire.fleet import FleetSpendView
from blizzard.wire.question import QuestionView
from blizzard.wire.runner import RunnerRegistryListResponse

_CHUNKS = TypeAdapter(ChunksPageView)
_RUNNERS = TypeAdapter(RunnerRegistryListResponse)
_QUESTIONS = TypeAdapter(list[QuestionView])
_SPEND = TypeAdapter(FleetSpendView)

# The since-the-beginning-of-time cutoff `hub status` passes ``GET /api/spend``.
_FLEET_SPEND_SINCE = "1970-01-01T00:00:00+00:00"


@dto
@dataclass(frozen=True)
class FleetStatus:
    chunks: list[ChunkSummary]
    runners: list[RunnerRow]
    questions: list[QuestionView]
    spend: FleetSpendView

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
    raw_chunks, pages = read_pages(cli, "/api/chunks", "GET /chunks", page=_CHUNKS, key="chunks", params=lens)
    raw_runners, _ = cli.decode(cli.get("/api/runners", "GET /runners"), _RUNNERS, "GET /runners")
    raw_questions, questions = cli.decode(
        cli.get("/api/questions", "GET /questions", params=lens), _QUESTIONS, "GET /questions"
    )
    raw_spend, spend = cli.decode(
        cli.get("/api/spend", "GET /spend", params={"since": _FLEET_SPEND_SINCE} | lens), _SPEND, "GET /spend"
    )
    # RunnerLine renders the registry rows as the hub sent them, each already validated above.
    runner_rows = cast(dict[str, Any], raw_runners).get("runners", [])
    view = FleetStatus(
        chunks=[row for page in pages for row in page.chunks],
        runners=cast(list[RunnerRow], runner_rows),
        questions=questions,
        spend=spend,
    )
    cli.show({"chunks": raw_chunks, "runners": raw_runners, "questions": raw_questions, "spend": raw_spend}, view)

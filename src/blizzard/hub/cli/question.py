"""``blizzard hub question`` — issue #104: operator verbs over open questions (list, answer)."""

from __future__ import annotations

from typing import Any

import click
import httpx

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing, QuestionRow


class QuestionListing(Listing):
    empty = "no open questions"

    def line(self, row: Any) -> str:
        return QuestionRow(row).line()


@click.group("question")
def question_group() -> None:
    """Operator verbs over open questions: list, answer."""


@question_group.command("list", cls=FleetCommand)
def question_list(cli: CliContext) -> None:
    """Every open (unanswered) question across the fleet."""
    rows = cli.get("/api/questions", "GET /questions").json()
    cli.show(rows, QuestionListing(rows))


@question_group.command("answer", cls=FleetCommand)
@click.argument("question_id")
@click.argument("answer_text")
@click.option("--by", "answered_by", default="operator", help="Who is answering (recorded on the row).")
def question_answer(cli: CliContext, question_id: str, answer_text: str, answered_by: str) -> None:
    """Answer an open question (first-write-wins CAS at the hub).

    A racing second answer loses and is told who already answered. A pure client of
    ``POST /api/questions/{id}/answers`` (issue #104)."""
    resp = cli.send(
        "post", f"/api/questions/{question_id}/answers", json_body={"answer": answer_text, "answered_by": answered_by}
    )
    if resp.status_code == httpx.codes.CONFLICT:
        winner = resp.json()
        raise click.ClickException(f"already answered by {winner.get('answered_by')}: {winner.get('answer')!r}")
    cli.check(resp, "POST /questions/{id}/answers", on_status={404: f"unknown question {question_id}"})
    cli.finish(resp, f"answered {question_id}: {answer_text!r} (the runner will resume the session)")

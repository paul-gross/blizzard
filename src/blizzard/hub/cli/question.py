"""``blizzard hub question`` — operator commands over open questions."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.project_option import project_option, project_query
from blizzard.hub.cli.views import Listing, QuestionLine


class QuestionListing(Listing):
    empty = "no open questions"

    def line(self, row: Any) -> str:
        return QuestionLine(row).line()


@click.group("question")
def question_group() -> None:
    """Read and answer the questions workers raise."""


@question_group.command("list", cls=FleetCommand)
@project_option(required_by_hub=False)
def question_list(cli: CliContext, project: str | None) -> None:
    """Every open (unanswered) question across the fleet, or only one project's."""
    rows = cli.get("/api/questions", "GET /questions", params=project_query(project)).json()
    cli.show(rows, QuestionListing(rows))


@question_group.command("answer", cls=FleetCommand)
@click.argument("question_id")
@click.argument("answer_text")
@click.option("--by", "answered_by", default="operator", help="Who is answering, recorded as the actor.")
def question_answer(cli: CliContext, question_id: str, answer_text: str, answered_by: str) -> None:
    """Answer an open question; the waiting chunk's runner then resumes its session.

    Only the first answer counts: a later one is refused and told who already answered.
    Refused too when the question's chunk has ended."""
    resp = cli.send(
        "post", f"/api/questions/{question_id}/answers", json_body={"answer": answer_text, "answered_by": answered_by}
    )
    if resp.status_code == HTTPStatus.CONFLICT:
        body = resp.json()
        if "answered_by" in body:
            raise click.ClickException(f"already answered by {body['answered_by']}: {body.get('answer')!r}")
        raise click.ClickException(str(body.get("detail", "question is closed")))
    cli.check(resp, "POST /questions/{id}/answers", on_status={404: f"unknown question {question_id}"})
    cli.finish(resp, f"answered {question_id}: {answer_text!r} (the runner will resume the session)")

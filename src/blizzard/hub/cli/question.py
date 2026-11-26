"""``blizzard hub question`` — operator commands over open questions."""

from __future__ import annotations

from typing import Any

import click
from pydantic import TypeAdapter

from blizzard.cli.refusal import refusal_of
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.project_option import project_option, project_query
from blizzard.hub.cli.views import Listing, QuestionLine
from blizzard.wire.question import QUESTION_ALREADY_ANSWERED, AnswerResult, QuestionView

_QUESTIONS = TypeAdapter(list[QuestionView])
_ANSWER = TypeAdapter(AnswerResult)


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
    raw, rows = cli.decode(
        cli.get("/api/questions", "GET /questions", params=project_query(project)), _QUESTIONS, "GET /questions"
    )
    cli.show(raw, QuestionListing(rows))


@question_group.command("answer", cls=FleetCommand)
@click.argument("question_id")
@click.argument("answer_text")
@click.option("--by", expose_value=False, deprecated=True, help="Ignored: the hub records the signed-in caller.")
def question_answer(cli: CliContext, question_id: str, answer_text: str) -> None:
    """Answer an open question; the waiting chunk's runner then resumes its session.

    Only the first answer counts: a later one is refused and told who already answered.
    Refused too when the question's chunk has ended."""
    resp = cli.send("post", f"/api/questions/{question_id}/answers", json_body={"answer": answer_text})
    refusal = refusal_of(resp)
    if refusal.type == QUESTION_ALREADY_ANSWERED:
        body = refusal.members
        raise click.ClickException(f"already answered by {body['answered_by']}: {body['answer']!r}")
    cli.check(resp, "POST /questions/{id}/answers")
    cli.finish(
        resp,
        f"answered {question_id}: {answer_text!r} (the runner will resume the session)",
        "POST /questions/{id}/answers",
        answer=_ANSWER,
    )

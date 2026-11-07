"""How a CLI process reads a hub refusal: the one adapter from a ``Response`` to the shared problem reading."""

from __future__ import annotations

from blizzard.cli.collaborators import Response
from blizzard.wire.problem_reading import ProblemReading, read_problem

#: A reading of a response whose body is not JSON, or has no refusal members.
_EMPTY = read_problem(None)


def refusal_of(resp: Response) -> ProblemReading:
    """The reading of ``resp``'s body; the empty reading when the body is not JSON."""
    try:
        return read_problem(resp.json())
    except ValueError:
        return _EMPTY

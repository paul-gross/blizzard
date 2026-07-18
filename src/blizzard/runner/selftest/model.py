"""The selftest job resource's data shapes — pure, no I/O (issue #54).

:class:`SelfTestRun` is the resource ``POST``/``GET /api/selftests`` mint and read
back; a resource with a result, not an RPC verb. Check names are module constants
(not a free-form string) so the CLI, the API view, and the checks runner all agree on
the same five identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SelfTestStatus = Literal["running", "passed", "failed"]

# The five adapter-drift checks, in the order a run performs them.
SPAWN_SESSION_ID = "spawn_session_id"
END_TO_END_EDIT_COMMIT = "end_to_end_edit_commit"
VERDICT_ELICITATION = "verdict_elicitation"
AUTOMATED_RESUME = "automated_resume"
RESUME_COMMAND = "resume_command"


@dataclass(frozen=True)
class SelfTestCheck:
    """One pass/fail check within a selftest run, with a human-readable detail."""

    name: str
    passed: bool
    detail: str


@dataclass
class SelfTestRun:
    """A selftest job resource: minted `running` by ``start``, filled in as checks
    complete, read back unchanged in between by ``get``.
    """

    id: str
    harness: str
    status: SelfTestStatus = "running"
    checks: list[SelfTestCheck] = field(default_factory=list)
    error: str | None = None

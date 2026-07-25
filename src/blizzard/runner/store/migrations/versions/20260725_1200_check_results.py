"""runner-side check results + checks-ran guard — check_results, checks_ran (runner store tree, issue #114)

The runner runs a node's ``checks:`` at worker exit, before the judgement is elicited,
and records each command's outcome as a durable fact so a runner kill between check-run
and judgement resumes at the right point without re-running or losing results (modeled on
``nudge_facts``/``attachments``).

- ``check_results`` — one row per check command per ``(lease_id, epoch)``, append-only,
  carrying ``passed`` + a bounded ``output_tail`` (the tail stays runner-local; only
  ``passed``/``command`` ride the wire to the hub's gate, [MF3]).
- ``checks_ran`` — the guard marker, at most one row per ``(lease_id, epoch)``, written
  AFTER the result rows and only for a node with a non-empty ``checks:``. On recovery it
  gates re-run: unset ⇒ re-run all (latest-wins, safe); set ⇒ read the recorded results
  back and judge.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern, mirroring ``20260719_1100_runner_nudge_facts``); this one creates
the two new tables ``checkfirst`` so a fresh ``base -> head`` and an in-place upgrade both
converge.

Revision ID: 20260725_1200_runner_check_results
Revises: 20260722_1000_runner_git_commit_declarations
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import check_results, checks_ran

revision: str = "20260725_1200_runner_check_results"
down_revision: str | None = "20260722_1000_runner_git_commit_declarations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (check_results, checks_ran)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)

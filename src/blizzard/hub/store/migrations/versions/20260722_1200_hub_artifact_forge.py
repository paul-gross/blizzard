"""artifacts.forge — the worker's declared origin, on the git_commit artifact row (issue #143 Phase 4)

Completes the declare-and-verify move (D2/R7): the worker declares
``(forge, repo, branch, commit)`` and the runner's read-only verify confirms the
forge against the leased env's own ``origin`` before submitting the ``git_commit``
artifact (``runner/loop/steps.py``'s ``_verify_and_collect_git_commits``). This
revision adds the column that carries it through storage, a nullable sibling of the
existing ``repo`` column: ``git_commit``-only, and null on every row recorded before
this column existed — those legacy rows read back as "the repo's origin"
(``hub/domain/artifacts.from_row``), the same tolerance
``20260721_1000_hub_escalation_decision_id`` gives ``decision_id``.

Idempotent like that revision: the column is added only where an older database
lacks it, so a fresh ``base -> head`` and an in-place upgrade both land at exactly
one column.

Revision ID: 20260722_1200_hub_artifact_forge
Revises: 20260721_1600_hub_event_log
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_1200_hub_artifact_forge"
down_revision: str | None = "20260721_1600_hub_event_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "artifacts"
_COLUMN = "forge"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)

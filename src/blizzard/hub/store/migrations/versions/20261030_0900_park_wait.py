"""Compatibility boundary for parked landing visits.

No schema change: the older hub cannot interpret parked polls or non-default landing policies.
Revision ID: 20261030_0900_park_wait
Revises: 20261029_1200_landing_policies
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261030_0900_park_wait"
down_revision: str | None = "20261029_1200_landing_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_graphs = sa.table("graphs", sa.column("graph_id"), sa.column("name"), sa.column("tenant_id"))
_nodes = sa.table("graph_nodes", sa.column("node_id"), sa.column("graph_id"))
_choices = sa.table("graph_choices", sa.column("node_id"), sa.column("name"))
_repositories = sa.table(
    "repositories", sa.column("repository_id"), sa.column("tenant_id"), sa.column("landing_policy")
)
_terms = sa.table("flow_submission_terms", sa.column("submission_id"), sa.column("landing_policy"))
_submissions = sa.table("flow_submissions", sa.column("submission_id"), sa.column("chunk_id"))
_closures = sa.table("flow_submission_closures", sa.column("submission_id"))
_polls = sa.table("hub_node_poll", sa.column("kind"))


def upgrade() -> None:
    bind = op.get_bind()
    conflicts = bind.execute(
        sa.select(_graphs.c.graph_id, _graphs.c.name, _graphs.c.tenant_id)
        .join(_nodes, _nodes.c.graph_id == _graphs.c.graph_id)
        .join(_choices, _choices.c.node_id == _nodes.c.node_id)
        .where(_choices.c.name == "awaiting-merge")
        .distinct()
    ).all()
    if conflicts:
        raise RuntimeError(f"graph choices conflict with reserved wait awaiting-merge: {conflicts}")


def downgrade() -> None:
    bind = op.get_bind()
    repositories = bind.execute(
        sa.select(_repositories.c.tenant_id, _repositories.c.repository_id, _repositories.c.landing_policy).where(
            _repositories.c.landing_policy != "merge-on-checks"
        )
    ).all()
    open_terms = bind.execute(
        sa.select(_submissions.c.chunk_id, _terms.c.submission_id, _terms.c.landing_policy)
        .join(_submissions, _terms.c.submission_id == _submissions.c.submission_id)
        .outerjoin(_closures, _terms.c.submission_id == _closures.c.submission_id)
        .where(_terms.c.landing_policy != "merge-on-checks", _closures.c.submission_id.is_(None))
    ).all()
    if repositories or open_terms:
        raise RuntimeError(
            f"settle landing policies before downgrade: repositories={repositories}; chunks={open_terms}"
        )
    closed_terms = sa.select(_closures.c.submission_id)
    bind.execute(
        sa.delete(_terms).where(_terms.c.landing_policy != "merge-on-checks", _terms.c.submission_id.in_(closed_terms))
    )
    bind.execute(sa.delete(_polls).where(_polls.c.kind == "awaiting-merge"))

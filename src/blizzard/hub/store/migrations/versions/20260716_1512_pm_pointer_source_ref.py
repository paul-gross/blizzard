"""pointer identity ``{provider, url}`` -> ``{source, ref}`` (hub store tree)

Reshapes ``chunk_pm_pointers`` in place; the backfill reads no configuration, so it is rehearsable.
Revision ID: 20260716_1512_hub_pm_pointer_source_ref
Revises: 20260716_1511_hub_runner_local_pause
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_1512_hub_pm_pointer_source_ref"
down_revision: str | None = "20260716_1511_hub_runner_local_pause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A local copy rather than an import: this revision must not move when the live adapter's
# own grammar does.
_ISSUE_RE = re.compile(r"(?:^|/)(?:repos/)?(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)")

# downgrade() reconstructs an issue URL under this constant placeholder: the real owner
# is not retained forward, and a constant is what keeps down-then-up stable.
_UNKNOWN_OWNER = "unknown"

_OLD_POINTERS = sa.Table(
    "chunk_pm_pointers",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("provider", sa.String, nullable=False),
    sa.Column("url", sa.String, nullable=False),
)

_NEW_POINTERS = sa.Table(
    "chunk_pm_pointers",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("source", sa.String, nullable=False),
    sa.Column("ref", sa.String, nullable=False),
)


def _backfill_source_ref(provider: str, url: str) -> tuple[str, str]:
    """The config-free, deterministic forward rule (see the module docstring)."""
    if provider == "github":
        match = _ISSUE_RE.search(url)
        if match is not None:
            return match["repo"], match["number"]
    return provider, url


def _reconstruct_provider_url(source: str, ref: str) -> tuple[str, str]:
    """The canonicalizing inverse (see the module docstring) — exact for a
    verbatim-copied row (non-numeric ``ref``), lossy-owner for a backfilled GitHub
    issue row (numeric ``ref``)."""
    if ref.isdigit():
        return "github", f"https://github.com/{_UNKNOWN_OWNER}/{source}/issues/{ref}"
    return source, ref


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("chunk_pm_pointers")}
    if "url" not in columns:
        return  # already reshaped (0011's skip-rows trick doesn't transfer to a column
        # reshape, so this guards the revision itself, not per-row)

    rows = bind.execute(sa.select(_OLD_POINTERS)).all()
    backfilled = [
        {"id": r.id, "chunk_id": r.chunk_id, "source": source, "ref": ref}
        for r in rows
        for source, ref in [_backfill_source_ref(r.provider, r.url)]
    ]

    with op.batch_alter_table("chunk_pm_pointers") as batch:
        batch.add_column(sa.Column("source", sa.String, nullable=True))
        batch.add_column(sa.Column("ref", sa.String, nullable=True))

    for row in backfilled:
        bind.execute(
            _NEW_POINTERS.update().where(_NEW_POINTERS.c.id == row["id"]).values(source=row["source"], ref=row["ref"])
        )

    with op.batch_alter_table("chunk_pm_pointers") as batch:
        batch.alter_column("source", nullable=False)
        batch.alter_column("ref", nullable=False)
        batch.drop_column("provider")
        batch.drop_column("url")


def downgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("chunk_pm_pointers")}
    if "provider" in columns:
        return  # already the pre-0013 shape

    rows = bind.execute(sa.select(_NEW_POINTERS)).all()
    reconstructed = [
        {"id": r.id, "chunk_id": r.chunk_id, "provider": provider, "url": url}
        for r in rows
        for provider, url in [_reconstruct_provider_url(r.source, r.ref)]
    ]

    with op.batch_alter_table("chunk_pm_pointers") as batch:
        batch.add_column(sa.Column("provider", sa.String, nullable=True))
        batch.add_column(sa.Column("url", sa.String, nullable=True))

    for row in reconstructed:
        bind.execute(
            _OLD_POINTERS.update()
            .where(_OLD_POINTERS.c.id == row["id"])
            .values(provider=row["provider"], url=row["url"])
        )

    with op.batch_alter_table("chunk_pm_pointers") as batch:
        batch.alter_column("provider", nullable=False)
        batch.alter_column("url", nullable=False)
        batch.drop_column("source")
        batch.drop_column("ref")

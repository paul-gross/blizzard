"""Remember the lease epoch floor when releasing an environment binding."""

import sqlalchemy as sa
from alembic import op

revision = "20261004_1000_binding_release_epoch_floor"
down_revision = "20261003_1000_runner_transcript_segment_spawn_cwd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("binding_releases", sa.Column("lease_epoch_floor", sa.Integer(), nullable=True))
    releases = sa.table(
        "binding_releases",
        sa.column("chunk_id", sa.String()),
        sa.column("released_at", sa.DateTime()),
        sa.column("lease_epoch_floor", sa.Integer()),
    )
    leases = sa.table(
        "leases",
        sa.column("lease_id", sa.String()),
        sa.column("chunk_id", sa.String()),
        sa.column("created_at", sa.DateTime()),
        sa.column("epoch", sa.Integer()),
    )
    closures = sa.table(
        "lease_closures", sa.column("lease_id", sa.String()), sa.column("closed_at", sa.DateTime()),
    )
    closed_by_release = (
        sa.select(closures.c.lease_id)
        .where(closures.c.lease_id == leases.c.lease_id)
        .where(closures.c.closed_at <= releases.c.released_at)
        .exists()
    )
    # Equal mint/release instants alone establish no ordering; the closure fact
    # identifies the old attempt, not a newly minted lease in the next tenure.
    floor = (
        sa.select(sa.func.max(leases.c.epoch))
        .where(leases.c.chunk_id == releases.c.chunk_id)
        .where(
            sa.or_(
                leases.c.created_at < releases.c.released_at,
                sa.and_(leases.c.created_at == releases.c.released_at, closed_by_release),
            )
        )
        .scalar_subquery()
    )
    op.execute(releases.update().values(lease_epoch_floor=floor))
    op.create_index("ix_leases_chunk_id_epoch", "leases", ["chunk_id", "epoch"])


def downgrade() -> None:
    op.drop_index("ix_leases_chunk_id_epoch", table_name="leases")
    op.drop_column("binding_releases", "lease_epoch_floor")

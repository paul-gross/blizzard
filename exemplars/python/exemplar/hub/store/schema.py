"""The permit tables of the hub store's SQLAlchemy metadata."""

# exemplar: bzh:tenant-keyed-table, bzh:facts-not-status, bzh:utc-instants.
# exemplar: production declares these in ``blizzard.hub.store.schema.metadata`` beside ``tenants``; the copy stands in.

from __future__ import annotations

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub.store import schema as hub_schema

metadata = MetaData()
tenants = hub_schema.tenants.to_metadata(metadata)
projects = hub_schema.projects.to_metadata(metadata)

permits = Table(
    "permits",
    metadata,
    Column("permit_id", String, nullable=False, info={"hub_unique": True}),  # pm_<ulid>
    Column("holder", String, nullable=False),
    Column("capability", String, nullable=False),
    Column("requested_at", UtcDateTime, nullable=False),
    Column("requested_by", String, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_permits_tenant_id"), nullable=False),
    Column("project_id", String, ForeignKey("projects.project_id", name="fk_permits_projects"), nullable=False),
    PrimaryKeyConstraint("permit_id", name="pk_permits"),
    UniqueConstraint(
        "tenant_id", "project_id", "holder", "capability", name="uq_permits_tenant_project_holder_capability"
    ),
)

# By ``requested_at`` then ``permit_id``: tenant-wide, per project, per holder.
Index("ix_permits_tenant_requested_at_permit_id", permits.c.tenant_id, permits.c.requested_at, permits.c.permit_id)
Index(
    "ix_permits_tenant_project_requested_at_permit_id",
    permits.c.tenant_id,
    permits.c.project_id,
    permits.c.requested_at,
    permits.c.permit_id,
)
Index(
    "ix_permits_tenant_holder_requested_at_permit_id",
    permits.c.tenant_id,
    permits.c.holder,
    permits.c.requested_at,
    permits.c.permit_id,
)

# Append-only.
permit_facts = Table(
    "permit_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("permit_id", String, ForeignKey("permits.permit_id", name="fk_permit_facts_permits"), nullable=False),
    Column("kind", String, nullable=False),
    Column("recorded_at", UtcDateTime, nullable=False),
    Column("recorded_by", String, nullable=False),
    Column("note", Text, nullable=True),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_permit_facts_tenant_id"), nullable=False),
)
Index("ix_permit_facts_tenant_permit_id_id", permit_facts.c.tenant_id, permit_facts.c.permit_id, permit_facts.c.id)

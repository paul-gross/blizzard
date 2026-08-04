"""pointer identity ``{provider, url}`` -> ``{source, ref}`` (hub store tree)

A PM pointer stops carrying a raw ``{provider, url}`` pair and instead names a
configured ``[[pm_source]]`` plus that source's own item reference: ``{source, ref}``.
``chunk_pm_pointers`` is reshaped in place (SQLite has no ``ALTER COLUMN``, so this uses
``op.batch_alter_table`` — the portable Alembic idiom, ``bzh:sql-portable``).

**Local ``sa.Table`` literals for both the old and new column shapes, not a
:mod:`blizzard.hub.store.schema` import:** a revision that *reshapes* a column is a data
migration pinned to a moment in time, and head-of-tree ``schema.py`` keeps moving
(``canon:no-retro``). Pinned by
``tests/test_pin_hub_api.py::test_pm_pointer_reshape_backfills_and_survives_a_down_then_up_cycle``.

**Backfill rule (config-free, deterministic — rehearsable):** this revision
reads no configuration file, so re-running it on the same bytes at two times gives the
same rows.

- ``provider == "github"`` and ``url`` is issue-shaped (``.../{owner}/{repo}/issues/{n}``)
  -> ``source = repo`` (the repo **tail**, not ``owner/repo`` — e.g. ``blizzard`` for
  ``paul-gross/blizzard``; source names are conventionally the repo tail), and
  ``ref = str(n)``. This is what lands the live rows on the configured name ``blizzard``
  rendering ``blizzard#26``.
- anything else -> ``source = provider``, ``ref = url`` verbatim — lossless; nothing
  destroyed, and (see ``downgrade`` below) exact for these rows on the way back.

A row whose backfilled ``source`` matches no ``[[pm_source]]`` the operator later
configures is not this migration's concern and must not fail it — refusing to boot
because a chunk that went ``done`` months ago names a retired source would be wrong. The
hub's pass-through routes already degrade a pointer with no matching configured source
to a null label; the composition root is where an operator would be warned of a
still-unresolved name, not a hard migration failure or a startup refusal.

**``downgrade()`` is canonicalizing, not byte-exact:** the *owner* segment was never
retained forward, so a numeric ``ref`` is reconstructed under the documented constant
placeholder ``_UNKNOWN_OWNER`` — structurally canonical, not resolvable. The constant is
what keeps **down-then-up stable** (re-upgrading returns the identical ``(source, ref)``),
and its accepted cost is that a downgraded hub running pre-0013 code 404s on every PM
read of a backfilled pointer until the chunk is re-ingested. Pinned by
``tests/test_pin_hub_api.py::test_pm_pointer_reshape_backfills_and_survives_a_down_then_up_cycle``.

A non-numeric ``ref`` was never GitHub-issue-shaped in the first place (the
verbatim-copy branch above): its downgrade is the exact inverse, ``provider=source``,
``url=ref``, with no loss at all. The ``ref.isdigit()`` discriminator is a heuristic —
the forward rule does not record which branch it took — so a hypothetical
*non-GitHub* row whose ``url`` was itself purely numeric (``provider="jira"``,
``url="123"``) reverses to a GitHub-shaped URL rather than its own bytes. No such row
exists in any live store (a bare number is not a URL), and down-then-up remains stable
for it regardless; it is recorded here rather than guarded against.

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

# The GitHub-shaped issue reference this backfill recognizes — an
# {owner}/{repo}/issues/{number} triple, with or without the REST /repos/ prefix. A
# local copy, not an import (see the module docstring): this revision must not move
# when the live GitHub adapter's own grammar does.
_ISSUE_RE = re.compile(r"(?:^|/)(?:repos/)?(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)")

# The documented, constant placeholder owner downgrade() reconstructs a GitHub issue
# URL under — the repo tail alone (this revision's forward output) cannot recover the
# real owner, and this is the deliberate, recorded cost of that.
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

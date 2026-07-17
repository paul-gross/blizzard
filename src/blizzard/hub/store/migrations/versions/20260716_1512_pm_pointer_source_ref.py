"""pointer identity ``{provider, url}`` -> ``{source, ref}`` (hub store tree, D-107)

A PM pointer stops carrying a raw ``{provider, url}`` pair and instead names a
configured ``[[pm_source]]`` plus that source's own item reference: ``{source, ref}``.
``chunk_pm_pointers`` is reshaped in place (SQLite has no ``ALTER COLUMN``, so this uses
``op.batch_alter_table`` — the portable Alembic idiom, ``bzh:sql-portable``).

**Deliberate deviation from 0011's shape:** this revision declares its own local
``sa.Table`` literals for both the old and new column shapes below, rather than
importing :mod:`blizzard.hub.store.schema` the way ``20260715_1817_chunk_promoted.py`` does.
0011 only *creates* a table, so importing head-of-tree shape was harmless; a revision
that *reshapes* a column is a data migration pinned to a moment in time; that module is
head-of-tree and will keep moving, so importing it here would silently change what this
revision does on a future checkout. A migration's meaning must not depend on when it is
read.

**Backfill rule (config-free, deterministic — D-099's rehearsability):** this revision
reads no configuration file, so re-running it on the same bytes at two times gives the
same rows.

- ``provider == "github"`` and ``url`` is issue-shaped (``.../{owner}/{repo}/issues/{n}``)
  -> ``source = repo`` (the repo **tail**, not ``owner/repo`` — e.g. ``blizzard`` for
  ``paul-gross/blizzard``; source names are conventionally the repo tail, D-108), and
  ``ref = str(n)``. This is what lands the live rows on the configured name ``blizzard``
  rendering ``blizzard#26``.
- anything else -> ``source = provider``, ``ref = url`` verbatim — lossless; nothing
  destroyed, and (see ``downgrade`` below) exact for these rows on the way back.

A row whose backfilled ``source`` matches no ``[[pm_source]]`` the operator later
configures is not this migration's concern and must not fail it — refusing to boot
because a chunk that went ``done`` months ago names a retired source would be wrong. The
hub's pass-through routes already degrade a pointer with no matching configured source
to a null label (D-108); the composition root is where an operator would be warned of a
still-unresolved name, not a hard migration failure or a startup refusal.

**``downgrade()`` is canonicalizing, not byte-exact (D-107):** reversing
``source="blizzard", ref="26"`` needs a full issue URL, but the *owner* segment
(``paul-gross`` in ``https://github.com/paul-gross/blizzard/issues/26``) was never
retained forward — only the repo tail was. That owner is genuinely unrecoverable from
``source`` alone. This revision's resolution: a numeric ``ref`` is treated as a
backfilled GitHub-issue row and reconstructed as ``provider="github"``,
``url=f"https://github.com/{_UNKNOWN_OWNER}/{source}/issues/{ref}"`` — *structurally*
canonical under a documented, constant placeholder owner, **not resolvable**: nothing
is served at that address (the real owner is gone, so no reconstruction could be).
That is the accepted, recorded cost (D-107), and its operational consequence is
concrete: **a downgraded hub running pre-0013 code parses that URL for owner/repo and
404s on every PM read** of a backfilled pointer until the chunk is re-ingested. A
rollback restores the *schema*, not the hub's PM reach.

What the placeholder buys — and the property actually worth holding — is that
**down-then-up is stable**: re-upgrading a downgraded row returns the identical
``(source, ref)``, because ``_backfill_source_ref`` reads only the repo tail and the
number, both of which survive the round trip. The owner is the only casualty, and it
is precisely the segment the forward rule already discarded. A reconstruction that
instead dropped the owner segment (an ``owner``-less ``{repo}/issues/{n}``) would fail
to re-parse and break that stability, which is why the placeholder is a constant and
not an omission.

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
# real owner, and this is the deliberate, recorded cost of that (D-107).
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

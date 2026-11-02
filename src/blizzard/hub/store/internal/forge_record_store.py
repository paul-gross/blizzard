"""SQLAlchemy adapter for the forge record seams (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Retired derives from the append-only
``forge_lifecycle_facts`` table, newest-fact-wins per id (``bzh:facts-not-status``). Every write commits its change row
in the same transaction, and every address it stores is canonical.

A forge an old address field names is created in the transaction of the repository or work-source write that names it
(:func:`insert_new_forge`). A forge's row is locked as a secret's is (:func:`lock_forge_row`): its retire and enable,
and every repository or work-source write naming it, take the lock first, so a retire and an enable racing on one forge
wait for each other's commit.
Through the forge transition every record naming a forge keeps its deprecated address columns equal to the forge's, so
a forge edit rewrites them on each referrer in the same transaction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.engine import Connection, Row
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.roles import adapter_model
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.errors import ConfigRevisionConflict
from blizzard.hub.domain.config.forges import (
    ConfiguredForge,
    ForgeFields,
    ForgeId,
    ForgeInUse,
    ForgeOriginTaken,
    ForgeRetired,
    ForgeSlug,
    ForgeSlugTaken,
    ForgeUnavailable,
    IWriteForgeRepository,
    NewForge,
    canonical_origin,
    require_unused,
    resolve_forge_reference,
)
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.forge_referrers import active_referrers
from blizzard.hub.store.internal.former_slugs import SlugTables
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.schema import (
    forge_former_slugs,
    forge_lifecycle_facts,
    forges,
    repositories,
    work_sources,
)
from blizzard.hub.store.scoped import ScopedConnections, statements_of

_SLUGS = SlugTables(forges, forge_former_slugs, "forge_id", ForgeSlugTaken)


def lock_forge_row(conn: Connection, forge_id: str) -> None:
    """Take the named forge row's write lock, as the first statement after the scoped write's tenant-row lock and
    before any guard read (``bzh:store-exclusive-write``). A forge's retire and enable and every referrer write take
    it, so one waits for the other's commit. A missing forge matches no row, and the caller's unknown-forge refusal
    still fires."""
    conn.execute(statements_of(conn).update(forges).where(forges.c.forge_id == forge_id).values(forge_id=forge_id))


@adapter_model
@dataclass(frozen=True)
class StandingForge:
    """What a referrer's write reads of the forge it names: its addresses, kind, slug, and whether it is retired."""

    forge_id: str
    slug: str
    kind: str
    api_base: str
    web_base: str | None
    retired: bool

    @property
    def label(self) -> str:
        return f"{self.slug} ({self.api_base})"


def standing_forge(conn: Connection, forge_id: str) -> StandingForge | None:
    """The forge ``forge_id`` names, inside the caller's transaction — ``None`` when there is none."""
    row = conn.execute(statements_of(conn).select(forges).where(forges.c.forge_id == forge_id)).first()
    if row is None:
        return None
    retired = forge_id in _retired_ids(conn, [ForgeId(forge_id)])
    return StandingForge(row.forge_id, row.slug, row.kind, row.api_base, row.web_base, retired)


def referred_forge(conn: Connection, forge_id: str, *, enabled: bool, enabling: bool = False) -> StandingForge:
    """The forge a repository or work-source write names, under its row lock: :class:`ForgeUnavailable` when it does
    not exist, or is retired while the record written is ``enabled``; :class:`ForgeRetired` when ``enabling`` a record
    onto a retired forge. A retired record keeps naming the forge it named, retired or not."""
    lock_forge_row(conn, forge_id)
    forge = standing_forge(conn, forge_id)
    if forge is None:
        raise ForgeUnavailable("forge", f"forge {forge_id} is unknown")
    if forge.retired and enabling:
        raise ForgeRetired(forge.slug, forge.api_base)
    if forge.retired and enabled:
        raise ForgeUnavailable("forge", f"forge {forge.label} is retired")
    return forge


def _retired_ids(conn: Connection, ids: list[ForgeId] | None = None) -> set[str]:
    facts = forge_lifecycle_facts
    found: set[str] = set()
    for batch in id_batches(ids) if ids is not None else [None]:
        found.update(conn.execute(newest_retired_select(statements_of(conn), facts, facts.c.forge_id, batch)).scalars())
    return found


def _origin_holder(conn: Connection, record: ConfiguredForge) -> str | None:
    row = conn.execute(
        statements_of(conn)
        .select(forges, forges.c.slug)
        .where(forges.c.api_base == record.fields.api_base, forges.c.forge_id != record.forge_id)
    ).first()
    return None if row is None else row.slug


def _columns(record: ConfiguredForge) -> dict[str, object]:
    f = record.fields
    return {
        "slug": record.slug,
        "name": record.name,
        "api_base": canonical_origin(f.api_base),
        "web_base": None if f.web_base is None else canonical_origin(f.web_base),
    }


def insert_forge(conn: Connection, record: ConfiguredForge, change: ConfigChange) -> None:
    """The create body, inside the caller's transaction. :class:`ForgeSlugTaken` or :class:`ForgeOriginTaken` when
    refused."""
    _SLUGS.claim(conn, record.forge_id, record.slug)
    holder = _origin_holder(conn, record)
    if holder is not None:
        raise ForgeOriginTaken(record.fields.api_base, holder=holder)
    conn.execute(
        statements_of(conn)
        .insert(forges)
        .values(
            forge_id=record.forge_id,
            kind=record.kind,
            **_columns(record),
            revision=record.revision,
            created_at=record.created_at,
            created_by=record.created_by,
        )
    )
    append_change(conn, change)


def update_forge(
    conn: Connection, record: ConfiguredForge, from_revision: int, left_slug: str | None, change: ConfigChange
) -> None:
    """The compare-and-set edit body, inside the caller's transaction, moving the slug when ``left_slug`` is set and
    rewriting every referrer's address columns."""
    lock_forge_row(conn, record.forge_id)
    if left_slug is not None:
        _SLUGS.claim(conn, record.forge_id, record.slug)
    holder = _origin_holder(conn, record)
    if holder is not None:
        raise ForgeOriginTaken(record.fields.api_base, holder=holder)
    columns = _columns(record)
    moved = conn.execute(
        statements_of(conn)
        .update(forges)
        .where(forges.c.forge_id == record.forge_id, forges.c.revision == from_revision)
        .values(**columns, revision=record.revision)
    ).rowcount
    if moved != 1:
        raise ConfigRevisionConflict("forge", record.forge_id, current=_revision(conn, record.forge_id))
    stmts = statements_of(conn)
    conn.execute(
        stmts.update(repositories)
        .where(repositories.c.forge_id == record.forge_id)
        .values(forge_api_url=columns["api_base"])
    )
    conn.execute(
        stmts.update(work_sources)
        .where(work_sources.c.forge_id == record.forge_id)
        .values(api_base=columns["api_base"], web_base=columns["web_base"])
    )
    if left_slug is not None:
        _SLUGS.release(conn, record.forge_id, left_slug, left_at=change.recorded_at)
    append_change(conn, change)


def record_forge_lifecycle(
    conn: Connection,
    record: ConfiguredForge,
    *,
    retired: bool,
    from_revision: int,
    at: datetime,
    by: str,
    change: ConfigChange,
) -> None:
    """The retire/enable body, inside the caller's transaction, under the forge's row lock. Retiring refuses
    :class:`ForgeInUse` while an enabled record names the forge."""
    lock_forge_row(conn, record.forge_id)
    if retired:
        require_unused(record.slug, active_referrers(conn, [record.forge_id])[record.forge_id])
    moved = conn.execute(
        statements_of(conn)
        .update(forges)
        .where(forges.c.forge_id == record.forge_id, forges.c.revision == from_revision)
        .values(revision=record.revision)
    ).rowcount
    if moved != 1:
        raise ConfigRevisionConflict("forge", record.forge_id, current=_revision(conn, record.forge_id))
    conn.execute(
        statements_of(conn)
        .insert(forge_lifecycle_facts)
        .values(forge_id=record.forge_id, retired=retired, set_at=at, set_by=by)
    )
    append_change(conn, change)


def _revision(conn: Connection, forge_id: str) -> int:
    return conn.execute(
        statements_of(conn).select(forges, forges.c.revision).where(forges.c.forge_id == forge_id)
    ).scalar_one()


def forge_holding(conn: Connection, api_base: str) -> ConfiguredForge | None:
    """The forge, retired or not, whose API origin is ``api_base``, inside the caller's transaction."""
    row = conn.execute(statements_of(conn).select(forges).where(forges.c.api_base == api_base)).first()
    return None if row is None else _reconstituted(conn, row)


def insert_new_forge(conn: Connection, forge: NewForge) -> None:
    """Create the forge a repository or work-source write makes, inside that write's transaction and in a savepoint of
    its own, so a refused insert leaves the write's transaction usable on Postgres. :class:`ForgeOriginTaken` or
    :class:`ForgeSlugTaken` when another forge holds its origin or slug — the caller decides the write again."""
    record, change = forge
    try:
        with conn.begin_nested():
            insert_forge(conn, record, change)
    except IntegrityError as exc:
        holder = _origin_holder(conn, record)
        if holder is not None:
            raise ForgeOriginTaken(record.fields.api_base, holder=holder) from exc
        raise ForgeSlugTaken(record.slug) from exc


class ForgeRecordStore:
    """Read-write forge adapter over the hub store."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def create(self, record: ConfiguredForge, *, change: ConfigChange) -> ConfiguredForge:
        try:
            with self._store.write("create", expect=(ForgeSlugTaken, ForgeOriginTaken, IntegrityError)) as conn:
                insert_forge(conn, record, change)
        except IntegrityError as exc:
            raise self._lost_race(record) from exc
        return record

    def update(
        self, record: ConfiguredForge, *, from_revision: int, left_slug: str | None, change: ConfigChange
    ) -> ConfiguredForge:
        try:
            with self._store.write(
                "update", expect=(ForgeSlugTaken, ForgeOriginTaken, ConfigRevisionConflict, IntegrityError)
            ) as conn:
                update_forge(conn, record, from_revision, left_slug, change)
        except IntegrityError as exc:
            raise self._lost_race(record) from exc
        return record

    def _lost_race(self, record: ConfiguredForge) -> ForgeOriginTaken | ForgeSlugTaken:
        """The refusal for a concurrent write that took the origin or the slug first, decided by a fresh read."""
        with self._store.read("lost_race") as conn:
            holder = _origin_holder(conn, record)
        return ForgeOriginTaken(record.fields.api_base, holder=holder) if holder else ForgeSlugTaken(record.slug)

    def record_lifecycle(
        self,
        record: ConfiguredForge,
        *,
        retired: bool,
        from_revision: int,
        at: datetime,
        by: str,
        change: ConfigChange,
    ) -> ConfiguredForge:
        with self._store.write("record_lifecycle", expect=(ForgeInUse, ConfigRevisionConflict)) as conn:
            record_forge_lifecycle(
                conn, record, retired=retired, from_revision=from_revision, at=at, by=by, change=change
            )
        return record

    def get(self, forge_id: ForgeId) -> ConfiguredForge | None:
        return self.get_many([forge_id]).get(forge_id)

    def get_many(self, forge_ids: list[ForgeId]) -> dict[ForgeId, ConfiguredForge]:
        if not forge_ids:
            return {}
        found: dict[ForgeId, ConfiguredForge] = {}
        with self._store.read("get_many") as conn:
            retired = _retired_ids(conn, forge_ids)
            for batch in id_batches(forge_ids):
                rows = conn.execute(self._store.select(forges).where(forges.c.forge_id.in_(batch))).all()
                found.update({ForgeId(row.forge_id): _of(row, retired=row.forge_id in retired) for row in rows})
        return found

    def list_all(self, *, include_retired: bool) -> list[ConfiguredForge]:
        with self._store.read("list_all") as conn:
            rows = conn.execute(self._store.select(forges).order_by(forges.c.slug)).all()
            retired = _retired_ids(conn)
        return [
            _of(row, retired=row.forge_id in retired) for row in rows if include_retired or row.forge_id not in retired
        ]

    def get_by_slug(self, slug: ForgeSlug) -> ConfiguredForge | None:
        with self._store.read("get_by_slug") as conn:
            row = conn.execute(self._store.select(forges).where(forges.c.slug == slug.value)).first()
            return None if row is None else _reconstituted(conn, row)

    def get_by_former_slug(self, slug: ForgeSlug) -> ConfiguredForge | None:
        left = self._store.scope_join(forge_former_slugs, forges, forge_former_slugs.c.forge_id == forges.c.forge_id)
        with self._store.read("get_by_former_slug") as conn:
            row = conn.execute(
                self._store.select(left, *forges.c).where(forge_former_slugs.c.slug == slug.value)
            ).first()
            return None if row is None else _reconstituted(conn, row)

    def get_by_api_base(self, api_base: str) -> ConfiguredForge | None:
        with self._store.read("get_by_api_base") as conn:
            return forge_holding(conn, api_base)

    def resolve(self, value: str) -> ConfiguredForge | None:
        return resolve_forge_reference(value, self)


def _reconstituted(conn: Connection, row: Row[Any]) -> ConfiguredForge:
    return _of(row, retired=row.forge_id in _retired_ids(conn, [ForgeId(row.forge_id)]))


def _of(row: Row[Any], *, retired: bool) -> ConfiguredForge:
    return ConfiguredForge(
        forge_id=ForgeId(row.forge_id),
        slug=row.slug,
        name=row.name,
        kind=row.kind,
        fields=ForgeFields(api_base=row.api_base, web_base=row.web_base),
        revision=row.revision,
        created_at=row.created_at,
        created_by=row.created_by,
        retired=retired,
    )


_forge_store_conforms: type[IWriteForgeRepository] = ForgeRecordStore

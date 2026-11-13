"""credential carry-over — every repository and work source that names a secret is given a credential.

Data only. Within every tenant: a referrer with no ``credential_id`` is linked to the credential already holding its
authority, ``secret/<secret_name>`` on its forge, if any; one ``github.token`` credential is created for each
(forge, secret) a referrer still names, shared by every referrer naming that pair, with no variant; the referrers are
linked again. A work source with no secret keeps no credential. A new credential's slug is its secret's name, suffixed
``-2``, ``-3``, … where the tenant holds it as the slug or former slug of any credential; it is created by
``migration:credential-carry-over`` at revision 1, with a ``create`` change row, and with a retiring lifecycle fact
where its secret is retired. A retired credential that an enabled referrer was linked to is then enabled, by an
``enable`` change row and a lifecycle fact by the same actor. ``secret_name`` is left as it is.

Every step inspects first and skips what a stopped run already did; a credential's id is minted from the tenant, forge
and secret, so a re-run mints the same one. The downgrade clears ``credential_id`` on every referrer naming a credential
this revision created, then deletes those credentials, their lifecycle facts, former slugs and change rows; a credential
a door created stays, and a credential this revision enabled is not retired again.
Revision ID: 20261113_1000_credential_carry_over
Revises: 20261113_0900_host_urls
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.migrations import migration_clock
from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261113_1000_credential_carry_over"
down_revision: str | None = "20261113_0900_host_urls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT = "tenant_id"
_CREDENTIAL_ID = "credential_id"
_ACTOR = "migration:credential-carry-over"
_DOOR = "migration"
_RECORD_KIND = "credential"
_BINDING = "github.token"
_SETTINGS = "{}"

# The prefixed-ULID mint and the slug rule, restated rather than imported (``bzh:frozen-revisions``).
_ID_PREFIX = "cred"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_CHARS = 26
_RANDOM_BITS = 80
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_OUTSIDE_SLUG = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 64
_NAME_MAX = 128
_FALLBACK_SLUG = "credential"

_credentials = sa.table(
    "credentials",
    sa.column(_CREDENTIAL_ID, sa.String),
    sa.column("slug", sa.String),
    sa.column("name", sa.String),
    sa.column("forge_id", sa.String),
    sa.column("binding", sa.String),
    sa.column("secret_name", sa.String),
    sa.column("settings", sa.Text),
    sa.column("authority", sa.String),
    sa.column("revision", sa.Integer),
    sa.column("created_at", UtcDateTime),
    sa.column("created_by", sa.String),
    sa.column(_TENANT, sa.String),
)
_former_slugs = sa.table(
    "credential_former_slugs",
    sa.column("slug", sa.String),
    sa.column(_CREDENTIAL_ID, sa.String),
    sa.column(_TENANT, sa.String),
)
_facts = sa.table(
    "credential_lifecycle_facts",
    sa.column("id", sa.Integer),
    sa.column(_CREDENTIAL_ID, sa.String),
    sa.column("retired", sa.Boolean),
    sa.column("set_at", UtcDateTime),
    sa.column("set_by", sa.String),
    sa.column(_TENANT, sa.String),
)
_secret_facts = sa.table(
    "secret_lifecycle_facts",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("retired", sa.Boolean),
    sa.column(_TENANT, sa.String),
)
_repository_facts = sa.table(
    "repository_lifecycle_facts",
    sa.column("id", sa.Integer),
    sa.column("repository_id", sa.String),
    sa.column("retired", sa.Boolean),
)
_source_facts = sa.table(
    "work_source_lifecycle_facts",
    sa.column("id", sa.Integer),
    sa.column("work_source_id", sa.String),
    sa.column("retired", sa.Boolean),
)
_changes = sa.table(
    "config_changes",
    sa.column("id", sa.Integer),
    sa.column("recorded_at", UtcDateTime),
    sa.column("actor", sa.String),
    sa.column("door", sa.String),
    sa.column("record_kind", sa.String),
    sa.column("record_key", sa.String),
    sa.column("revision", sa.Integer),
    sa.column("op", sa.String),
    sa.column("diff", sa.Text),
    sa.column("apply_id", sa.String),
    sa.column(_TENANT, sa.String),
    sa.column("project_id", sa.String),
)
_repositories = sa.table(
    "repositories",
    sa.column("repository_id", sa.String),
    sa.column(_TENANT, sa.String),
    sa.column("forge_id", sa.String),
    sa.column("secret_name", sa.String),
    sa.column(_CREDENTIAL_ID, sa.String),
)
_sources = sa.table(
    "work_sources",
    sa.column("work_source_id", sa.String),
    sa.column(_TENANT, sa.String),
    sa.column("forge_id", sa.String),
    sa.column("secret_name", sa.String),
    sa.column(_CREDENTIAL_ID, sa.String),
)

# Each referring table: its table, id column, and lifecycle facts.
_REFERRERS = (
    (_repositories, "repository_id", _repository_facts),
    (_sources, "work_source_id", _source_facts),
)


# --- the mint and the slug rule, frozen -----------------------------------------------------------------------------


def _encode(value: int) -> str:
    chars: list[str] = []
    for _ in range(_ULID_CHARS):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def _minted(tenant_id: str, forge_id: str, secret_name: str, at: datetime) -> str:
    """The id of the credential ``tenant_id`` holds for ``secret_name`` on ``forge_id``: timestamped at its creation,
    the same every run."""
    millis = max(0, (at - _EPOCH) // timedelta(milliseconds=1))
    key = f"credential/{tenant_id}/{forge_id}/{secret_name}"
    drawn = int.from_bytes(hashlib.sha256(key.encode()).digest()[: _RANDOM_BITS // 8], "big")
    return f"{_ID_PREFIX}_{_encode((millis << _RANDOM_BITS) | drawn)}"


def _derived(raw: str) -> str:
    """The slug closest to ``raw``: lowercased, each run outside ``[a-z0-9]`` one hyphen, trimmed, cut to the bound."""
    derived = _OUTSIDE_SLUG.sub("-", raw.lower()).strip("-")[:_SLUG_MAX].strip("-")
    return derived or _FALLBACK_SLUG


def _suffixed(base: str, taken: set[str]) -> str:
    """``base``, or the first ``base-2``, ``base-3``, … the tenant does not hold, cut to the bound."""
    if base not in taken:
        return base
    number = 2
    while True:
        tail = f"-{number}"
        candidate = f"{base[: _SLUG_MAX - len(tail)].rstrip('-')}{tail}"
        if candidate not in taken:
            return candidate
        number += 1


# --- reading ----------------------------------------------------------------------------------------------------


def _retired(bind: sa.Connection, facts: sa.TableClause, *key: str) -> set[tuple[object, ...]]:
    """The keys (``key`` columns) whose newest lifecycle fact retires them."""
    newest = sa.select(sa.func.max(facts.c.id)).group_by(*(facts.c[k] for k in key))
    rows = bind.execute(sa.select(*(facts.c[k] for k in key)).where(facts.c.id.in_(newest), facts.c.retired.is_(True)))
    return {tuple(row) for row in rows}


def _authorities(bind: sa.Connection) -> dict[tuple[str, str, str], str]:
    """Each credential's id by the tenant, forge and authority it holds."""
    rows = bind.execute(
        sa.select(
            _credentials.c[_TENANT], _credentials.c.forge_id, _credentials.c.authority, _credentials.c[_CREDENTIAL_ID]
        )
    )
    return {(t, f, a): c for t, f, a, c in rows}


def _link(bind: sa.Connection) -> set[tuple[str, str]]:
    """Point each referrer with a secret and no credential at the credential holding that secret's authority on its
    forge, where one exists. Returns the (table name, record id) of each referrer linked."""
    held = _authorities(bind)
    linked: set[tuple[str, str]] = set()
    for table, key, _ in _REFERRERS:
        rows = bind.execute(
            sa.select(table.c[key], table.c[_TENANT], table.c.forge_id, table.c.secret_name).where(
                table.c[_CREDENTIAL_ID].is_(None), table.c.secret_name.is_not(None)
            )
        ).all()
        for record_id, tenant_id, forge_id, secret_name in rows:
            credential_id = held.get((tenant_id, forge_id, f"secret/{secret_name}"))
            if credential_id is None:
                continue
            bind.execute(sa.update(table).where(table.c[key] == record_id).values({_CREDENTIAL_ID: credential_id}))
            linked.add((table.name, record_id))
    return linked


def _created_diff(slug: str, name: str, forge_id: str, secret_name: str) -> str:
    fields: dict[str, object] = {
        "slug": slug,
        "name": name,
        "forge": forge_id,
        "binding": _BINDING,
        "secret": secret_name,
        "settings": {},
    }
    return json.dumps([{"field": field, "old": None, "new": value} for field, value in fields.items()])


def _enabled_diff() -> str:
    return json.dumps([{"field": "retired", "old": True, "new": False}])


def _create(bind: sa.Connection, at: datetime) -> None:
    """Create the credential, its change row, and its retiring fact where the secret is retired, for each (forge,
    secret) a referrer still names with no credential. A stopped run is caught by the link step, which finds what it
    created by its authority, so a re-run creates none of them again."""
    wanted: set[tuple[str, str, str]] = set()
    for table, _, _ in _REFERRERS:
        rows = bind.execute(
            sa.select(table.c[_TENANT], table.c.forge_id, table.c.secret_name).where(
                table.c[_CREDENTIAL_ID].is_(None), table.c.secret_name.is_not(None)
            )
        )
        wanted.update((t, f, s) for t, f, s in rows)
    if not wanted:
        return
    retired_secrets = _retired(bind, _secret_facts, _TENANT, "name")
    taken: dict[str, set[str]] = {}
    for tenant_id, slug in bind.execute(sa.select(_credentials.c[_TENANT], _credentials.c.slug)):
        taken.setdefault(tenant_id, set()).add(slug)
    for tenant_id, slug in bind.execute(sa.select(_former_slugs.c[_TENANT], _former_slugs.c.slug)):
        taken.setdefault(tenant_id, set()).add(slug)
    for tenant_id, forge_id, secret_name in sorted(wanted):
        held = taken.setdefault(tenant_id, set())
        slug = _suffixed(_derived(secret_name), held)
        held.add(slug)
        name = secret_name[:_NAME_MAX]
        credential_id = _minted(tenant_id, forge_id, secret_name, at)
        bind.execute(
            sa.insert(_credentials).values(
                {
                    _CREDENTIAL_ID: credential_id,
                    "slug": slug,
                    "name": name,
                    "forge_id": forge_id,
                    "binding": _BINDING,
                    "secret_name": secret_name,
                    "settings": _SETTINGS,
                    "authority": f"secret/{secret_name}",
                    "revision": 1,
                    "created_at": at,
                    "created_by": _ACTOR,
                    _TENANT: tenant_id,
                }
            )
        )
        bind.execute(
            sa.insert(_changes).values(
                recorded_at=at,
                actor=_ACTOR,
                door=_DOOR,
                record_kind=_RECORD_KIND,
                record_key=credential_id,
                revision=1,
                op="create",
                diff=_created_diff(slug, name, forge_id, secret_name),
                apply_id=None,
                tenant_id=tenant_id,
                project_id=None,
            )
        )
        if (tenant_id, secret_name) in retired_secrets:
            bind.execute(
                sa.insert(_facts).values(
                    {_CREDENTIAL_ID: credential_id, "retired": True, "set_at": at, "set_by": _ACTOR, _TENANT: tenant_id}
                )
            )


def _enable(bind: sa.Connection, at: datetime, linked: set[tuple[str, str]]) -> None:
    """Enable each retired credential an enabled referrer linked by this run names."""
    retired_credentials = {key for (key,) in _retired(bind, _facts, _CREDENTIAL_ID)}
    enabling: dict[str, str] = {}
    for table, key, facts in _REFERRERS:
        retired_records = {record for (record,) in _retired(bind, facts, key)}
        rows = bind.execute(
            sa.select(table.c[key], table.c[_CREDENTIAL_ID], table.c[_TENANT]).where(
                table.c[_CREDENTIAL_ID].is_not(None)
            )
        )
        for record_id, credential_id, tenant_id in rows:
            if (
                credential_id in retired_credentials
                and record_id not in retired_records
                and (table.name, record_id) in linked
            ):
                enabling[credential_id] = tenant_id
    for credential_id, tenant_id in sorted(enabling.items()):
        revision_now = bind.execute(
            sa.select(_credentials.c.revision).where(
                _credentials.c[_TENANT] == tenant_id, _credentials.c[_CREDENTIAL_ID] == credential_id
            )
        ).scalar_one()
        bind.execute(
            sa.update(_credentials)
            .where(_credentials.c[_TENANT] == tenant_id, _credentials.c[_CREDENTIAL_ID] == credential_id)
            .values(revision=revision_now + 1)
        )
        bind.execute(
            sa.insert(_facts).values(
                {_CREDENTIAL_ID: credential_id, "retired": False, "set_at": at, "set_by": _ACTOR, _TENANT: tenant_id}
            )
        )
        bind.execute(
            sa.insert(_changes).values(
                recorded_at=at,
                actor=_ACTOR,
                door=_DOOR,
                record_kind=_RECORD_KIND,
                record_key=credential_id,
                revision=revision_now + 1,
                op="enable",
                diff=_enabled_diff(),
                apply_id=None,
                tenant_id=tenant_id,
                project_id=None,
            )
        )


def upgrade() -> None:
    bind = op.get_bind()
    at = migration_clock().now()
    linked = _link(bind)
    _create(bind, at)
    linked |= _link(bind)
    _enable(bind, at, linked)


def downgrade() -> None:
    bind = op.get_bind()
    created = sa.select(_credentials.c[_CREDENTIAL_ID]).where(_credentials.c.created_by == _ACTOR)
    ids = list(bind.execute(created).scalars())
    if not ids:
        return
    for table, _, _ in _REFERRERS:
        bind.execute(sa.update(table).where(table.c[_CREDENTIAL_ID].in_(ids)).values({_CREDENTIAL_ID: None}))
    bind.execute(sa.delete(_facts).where(_facts.c[_CREDENTIAL_ID].in_(ids)))
    bind.execute(sa.delete(_former_slugs).where(_former_slugs.c[_CREDENTIAL_ID].in_(ids)))
    bind.execute(sa.delete(_changes).where(_changes.c.record_kind == _RECORD_KIND, _changes.c.record_key.in_(ids)))
    bind.execute(sa.delete(_credentials).where(_credentials.c[_CREDENTIAL_ID].in_(ids)))

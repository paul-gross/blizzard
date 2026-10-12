"""tenant keys — every table whose natural key changes is rebuilt keyed by its tenant, and tightened in the same copy.

Each table in :data:`_PLAN` is brought to the shape listed there: its primary key, unique constraints, listing indexes
and id-lookup indexes led by ``tenant_id``, a foreign key from a child onto a rebuilt parent widened to
``(tenant_id, <key>)``, ``tenant_id`` made ``NOT NULL`` with a named foreign key to ``tenants``. Every constraint and
index the revision creates carries its name; the constraints it keeps unchanged keep theirs, or stay unnamed. One batch
per table under ``recreate="auto"`` rebuilds it (SQLite copies the table; other dialects alter it in place), except a
foreign key onto another rebuilt table: it is dropped in its table's batch, which runs before its parent's, and added
back widened in a second batch once every parent holds its new key — no dialect has to drop a key a live foreign key
still names. ``egress_cursor`` gains ``events_tenant_id``, the tenant of the events position it records, null on every
existing row.

``tenant_id`` was stamped on every row by ``20261008_1200_tenant_id_backfill``; while a table still holds a row without
one the upgrade refuses, naming it, before any table is touched. Re-running the upgrade skips every table already at
its target shape and adds only the widened foreign keys still missing. The downgrade refuses while more than one tenant
exists, naming them; with one it restores :data:`_PRIOR` — the keys, names and nullable ``tenant_id`` those tables had
before — and drops ``events_tenant_id``.
Revision ID: 20261008_1300_tenant_keys
Revises: 20261008_1200_tenant_id_backfill
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_1300_tenant_keys"
down_revision: str | None = "20261008_1200_tenant_id_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_COLUMN = "tenant_id"
# Once two tenants may hold the same derivation key, the egress events position names its tenant too.
_CURSOR = "egress_cursor"
_CURSOR_TENANT = "events_tenant_id"

# The target shape of every table this revision rebuilds — frozen here, never derived from ``schema.metadata``
# (``bzh:frozen-revisions``).
# --- M-05 begin ---
_Key = tuple[str | None, tuple[str, ...]]  # (name, columns)
_ForeignKey = tuple[str | None, tuple[str, ...], str, tuple[str, ...]]  # (name, columns, parent, parent columns)
_IndexPlan = tuple[str, tuple[str, ...], bool]  # (name, columns, unique)
# (table, primary key, uniques, foreign keys, indexes, tenant_id nullable)
_TablePlan = tuple[str, _Key, tuple[_Key, ...], tuple[_ForeignKey, ...], tuple[_IndexPlan, ...], bool]
_PLAN: tuple[_TablePlan, ...] = (
    (
        "answer_deliveries",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_answer_deliveries_questions", ("tenant_id", "question_id"), "questions", ("tenant_id", "question_id")),
            ("fk_answer_deliveries_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_answer_deliveries_tenant_question_id", ("tenant_id", "question_id"), False),),
        False,
    ),
    (
        "chunk_work_refs",
        (None, ("id",)),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_chunk_work_refs_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_chunk_work_refs_tenant_chunk_id", ("tenant_id", "chunk_id"), False),
            ("ix_chunk_work_refs_tenant_source_ref", ("tenant_id", "source", "ref"), False),
        ),
        False,
    ),
    (
        "garden_proposal_closures",
        (None, ("id",)),
        (("uq_garden_proposal_closures_proposal_id", ("proposal_id",)),),
        (
            (None, ("proposal_id",), "garden_proposals", ("proposal_id",)),
            ("fk_garden_proposal_closures_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_garden_proposal_closures_tenant_source_ref", ("tenant_id", "source", "ref"), True),),
        False,
    ),
    (
        "graphs",
        (None, ("graph_id",)),
        (),
        (("fk_graphs_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (("ix_graphs_tenant_name", ("tenant_id", "name"), False),),
        False,
    ),
    (
        "keyed_locks",
        ("pk_keyed_locks", ("tenant_id", "namespace", "key")),
        (),
        (("fk_keyed_locks_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (),
        False,
    ),
    (
        "question_answers",
        ("pk_question_answers", ("tenant_id", "question_id")),
        (),
        (
            ("fk_question_answers_questions", ("tenant_id", "question_id"), "questions", ("tenant_id", "question_id")),
            ("fk_question_answers_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_question_answers_tenant_answered_at_question_id", ("tenant_id", "answered_at", "question_id"), False),),
        False,
    ),
    (
        "questions",
        ("pk_questions", ("tenant_id", "question_id")),
        (),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_questions_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_questions_tenant_asked_at_question_id", ("tenant_id", "asked_at", "question_id"), False),
            ("ix_questions_tenant_chunk_id", ("tenant_id", "chunk_id"), False),
        ),
        False,
    ),
    (
        "repositories",
        ("pk_repositories", ("tenant_id", "name")),
        (("uq_repositories_tenant_coordinate", ("tenant_id", "forge_api_url", "owner", "repo")),),
        (
            ("fk_repositories_secrets", ("tenant_id", "secret_name"), "secrets", ("tenant_id", "name")),
            ("fk_repositories_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (),
        False,
    ),
    (
        "repository_lifecycle_facts",
        (None, ("id",)),
        (),
        (
            (
                "fk_repository_lifecycle_facts_repositories",
                ("tenant_id", "name"),
                "repositories",
                ("tenant_id", "name"),
            ),
            ("fk_repository_lifecycle_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_repository_lifecycle_facts_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "routines",
        (None, ("routine_id",)),
        (("uq_routines_tenant_name", ("tenant_id", "name")),),
        (
            (None, ("default_scope_slug",), "scopes", ("slug",)),
            ("fk_routines_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (),
        False,
    ),
    (
        "secret_lifecycle_facts",
        (None, ("id",)),
        (),
        (
            ("fk_secret_lifecycle_facts_secrets", ("tenant_id", "name"), "secrets", ("tenant_id", "name")),
            ("fk_secret_lifecycle_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (("ix_secret_lifecycle_facts_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "secrets",
        ("pk_secrets", ("tenant_id", "name")),
        (),
        (("fk_secrets_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (),
        False,
    ),
    (
        "transcript_event_derivations",
        ("pk_transcript_event_derivations", ("tenant_id", "segment_id", "extractor_version")),
        (),
        (("fk_transcript_event_derivations_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (
            (
                "ix_transcript_event_derivations_egress_order",
                ("derived_at", "segment_id", "extractor_version", "tenant_id"),
                False,
            ),
            (
                "ix_transcript_event_derivations_tenant_derived_at",
                ("tenant_id", "derived_at", "segment_id", "extractor_version"),
                False,
            ),
        ),
        False,
    ),
    (
        "transcript_events",
        (None, ("id",)),
        (
            (
                "uq_transcript_events_tenant_natural_key",
                ("tenant_id", "segment_id", "extractor_version", "kind", "turn_path", "occurrence"),
            ),
        ),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_transcript_events_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_transcript_events_extractor_version_id", ("extractor_version", "id"), False),
            ("ix_transcript_events_tenant_chunk_id", ("tenant_id", "chunk_id"), False),
            ("ix_transcript_events_tenant_subject", ("tenant_id", "subject"), False),
            ("ix_transcript_events_tenant_tool", ("tenant_id", "tool"), False),
        ),
        False,
    ),
    (
        "transcript_segments",
        (None, ("id",)),
        (("uq_transcript_segments_tenant_segment_turn_start", ("tenant_id", "segment_id", "turn_range_start")),),
        (
            (None, ("chunk_id",), "chunks", ("chunk_id",)),
            ("fk_transcript_segments_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (
            ("ix_transcript_segments_final_chunk_id", ("final", "chunk_id"), False),
            (
                "ix_transcript_segments_tenant_chunk_id",
                ("tenant_id", "chunk_id", "segment_id", "turn_range_start"),
                False,
            ),
            ("ix_transcript_segments_tenant_runner_received_at", ("tenant_id", "runner_id", "received_at"), False),
            ("ix_transcript_segments_tenant_supersedes", ("tenant_id", "supersedes"), False),
        ),
        False,
    ),
    (
        "work_item_sequence",
        ("pk_work_item_sequence", ("tenant_id", "source")),
        (),
        (("fk_work_item_sequence_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (),
        False,
    ),
    (
        "work_items",
        (None, ("work_item_id",)),
        (("uq_work_items_tenant_source_ref", ("tenant_id", "source", "ref")),),
        (("fk_work_items_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),),
        (("ix_work_items_tenant_source", ("tenant_id", "source"), False),),
        False,
    ),
    (
        "work_source_lifecycle_facts",
        (None, ("id",)),
        (),
        (
            ("fk_work_source_lifecycle_facts_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
            (
                "fk_work_source_lifecycle_facts_work_sources",
                ("tenant_id", "name"),
                "work_sources",
                ("tenant_id", "name"),
            ),
        ),
        (("ix_work_source_lifecycle_facts_tenant_id", ("tenant_id",), False),),
        False,
    ),
    (
        "work_sources",
        ("pk_work_sources", ("tenant_id", "name")),
        (("uq_work_sources_tenant_provider_locator", ("tenant_id", "provider", "locator")),),
        (
            ("fk_work_sources_secrets", ("tenant_id", "secret_name"), "secrets", ("tenant_id", "name")),
            ("fk_work_sources_tenant_id", ("tenant_id",), "tenants", ("tenant_id",)),
        ),
        (),
        False,
    ),
)
# --- M-05 end ---

# The same tables as 20261008_1200_tenant_id_backfill left them — the shape the downgrade restores.
_PRIOR: tuple[_TablePlan, ...] = (
    (
        "answer_deliveries",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)), (None, ("question_id",), "questions", ("question_id",))),
        (),
        True,
    ),
    (
        "chunk_work_refs",
        (None, ("id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_chunk_work_refs_chunk_id", ("chunk_id",), False),
            ("ix_chunk_work_refs_source_ref", ("source", "ref"), False),
        ),
        True,
    ),
    (
        "garden_proposal_closures",
        (None, ("id",)),
        (("uq_garden_proposal_closures_proposal_id", ("proposal_id",)),),
        ((None, ("proposal_id",), "garden_proposals", ("proposal_id",)),),
        (("ix_garden_proposal_closures_source_ref", ("source", "ref"), True),),
        True,
    ),
    ("graphs", (None, ("graph_id",)), (), (), (("ix_graphs_name", ("name",), False),), True),
    ("keyed_locks", (None, ("namespace", "key")), (), (), (), True),
    (
        "question_answers",
        (None, ("question_id",)),
        (),
        ((None, ("question_id",), "questions", ("question_id",)),),
        (("ix_question_answers_answered_at_question_id", ("answered_at", "question_id"), False),),
        True,
    ),
    (
        "questions",
        (None, ("question_id",)),
        (),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_questions_asked_at_question_id", ("asked_at", "question_id"), False),
            ("ix_questions_chunk_id", ("chunk_id",), False),
        ),
        True,
    ),
    (
        "repositories",
        (None, ("name",)),
        (("uq_repositories_coordinate", ("forge_api_url", "owner", "repo")),),
        ((None, ("secret_name",), "secrets", ("name",)),),
        (),
        True,
    ),
    ("repository_lifecycle_facts", (None, ("id",)), (), ((None, ("name",), "repositories", ("name",)),), (), True),
    (
        "routines",
        (None, ("routine_id",)),
        (("uq_routines_name", ("name",)),),
        ((None, ("default_scope_slug",), "scopes", ("slug",)),),
        (),
        True,
    ),
    ("secret_lifecycle_facts", (None, ("id",)), (), ((None, ("name",), "secrets", ("name",)),), (), True),
    ("secrets", (None, ("name",)), (), (), (), True),
    (
        "transcript_event_derivations",
        (None, ("segment_id", "extractor_version")),
        (),
        (),
        (
            (
                "ix_transcript_event_derivations_derived_at_segment_id",
                ("derived_at", "segment_id", "extractor_version"),
                False,
            ),
        ),
        True,
    ),
    (
        "transcript_events",
        (None, ("id",)),
        (("uq_transcript_events_natural_key", ("segment_id", "extractor_version", "kind", "turn_path", "occurrence")),),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_transcript_events_chunk_id", ("chunk_id",), False),
            ("ix_transcript_events_extractor_version_id", ("extractor_version", "id"), False),
            ("ix_transcript_events_segment_id", ("segment_id",), False),
            ("ix_transcript_events_subject", ("subject",), False),
            ("ix_transcript_events_tool", ("tool",), False),
        ),
        True,
    ),
    (
        "transcript_segments",
        (None, ("id",)),
        (("uq_transcript_segments_segment_turn_start", ("segment_id", "turn_range_start")),),
        ((None, ("chunk_id",), "chunks", ("chunk_id",)),),
        (
            ("ix_transcript_segments_chunk_id", ("chunk_id",), False),
            ("ix_transcript_segments_final_chunk_id", ("final", "chunk_id"), False),
            ("ix_transcript_segments_runner_received_at", ("runner_id", "received_at"), False),
            ("ix_transcript_segments_segment_id", ("segment_id",), False),
            ("ix_transcript_segments_supersedes", ("supersedes",), False),
        ),
        True,
    ),
    ("work_item_sequence", (None, ("source",)), (), (), (), True),
    (
        "work_items",
        (None, ("work_item_id",)),
        (("uq_work_items_source_ref", ("source", "ref")),),
        (),
        (("ix_work_items_source", ("source",), False),),
        True,
    ),
    ("work_source_lifecycle_facts", (None, ("id",)), (), ((None, ("name",), "work_sources", ("name",)),), (), True),
    (
        "work_sources",
        (None, ("name",)),
        (("uq_work_sources_provider_locator", ("provider", "locator")),),
        ((None, ("secret_name",), "secrets", ("name",)),),
        (),
        True,
    ),
)


# A table's shape as compared: primary key, then unique constraints, foreign keys and indexes as sets, then whether
# ``tenant_id`` is nullable.
_Shape = tuple[_Key, frozenset[_Key], frozenset[_ForeignKey], frozenset[_IndexPlan], bool]

_tenants = sa.table("tenants", sa.column("tenant_id", sa.String))


def _tenant_ids(bind: sa.Connection) -> list[str]:
    return list(bind.execute(sa.select(_tenants.c.tenant_id).order_by(_tenants.c.tenant_id)).scalars())


def _linked(plans: tuple[_TablePlan, ...]) -> frozenset[str]:
    """The names of the planned foreign keys onto another table this revision rebuilds — the second-batch keys."""
    tables = {plan[0] for plan in plans}
    return frozenset(name for plan in plans for name, _, parent, _ in plan[3] if parent in tables and name is not None)


def _level(table: str, parents: dict[str, set[str]]) -> int:
    return 1 + max((_level(parent, parents) for parent in parents[table]), default=-1)


def _children_first(plans: tuple[_TablePlan, ...]) -> list[_TablePlan]:
    """The plans ordered so every table precedes each table its foreign keys point at among them."""
    tables = {plan[0] for plan in plans}
    parents = {plan[0]: {parent for _, _, parent, _ in plan[3] if parent in tables} - {plan[0]} for plan in plans}
    return sorted(plans, key=lambda plan: -_level(plan[0], parents))


def _wanted(plan: _TablePlan, ignored: frozenset[str]) -> _Shape:
    _, primary_key, uniques, foreign_keys, indexes, nullable = plan
    return (
        primary_key,
        frozenset(uniques),
        frozenset(fk for fk in foreign_keys if fk[0] not in ignored),
        frozenset(indexes),
        nullable,
    )


def _shape(bind: sa.Connection, table: str, ignored: frozenset[str]) -> _Shape:
    inspector = sa.inspect(bind)
    primary_key = inspector.get_pk_constraint(table)
    tenant_nullable = next(c["nullable"] for c in inspector.get_columns(table) if c["name"] == _TENANT_COLUMN)
    return (
        (primary_key.get("name"), tuple(primary_key["constrained_columns"])),
        frozenset((u["name"], tuple(u["column_names"])) for u in inspector.get_unique_constraints(table)),
        frozenset(
            (fk.get("name"), tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
            for fk in inspector.get_foreign_keys(table)
            if fk.get("name") not in ignored
        ),
        frozenset(
            (str(i["name"]), tuple(str(c) for c in i["column_names"]), bool(i["unique"]))
            for i in inspector.get_indexes(table)
            if not i.get("duplicates_constraint")
        ),
        bool(tenant_nullable),
    )


def _matched(current: _Shape, wanted: _Shape) -> _Shape:
    """``current`` with every reflected name the plan leaves unnamed read as unnamed, so a plan's ``None`` matches any
    name on the same columns: SQLite reflects an unnamed constraint with no name, other dialects with the one they
    assigned it."""
    (name, columns), uniques, foreign_keys, indexes, nullable = current
    (wanted_name, wanted_columns), wanted_uniques, wanted_foreign_keys, _, _ = wanted
    unnamed_uniques = {key for key_name, key in wanted_uniques if key_name is None}
    unnamed_foreign_keys = {fk[1:] for fk in wanted_foreign_keys if fk[0] is None}
    return (
        (None if wanted_name is None and columns == wanted_columns else name, columns),
        frozenset((None if key in unnamed_uniques else key_name, key) for key_name, key in uniques),
        frozenset((None, *fk[1:]) if fk[1:] in unnamed_foreign_keys else fk for fk in foreign_keys),
        indexes,
        nullable,
    )


def _refuse_unstamped(bind: sa.Connection, plans: tuple[_TablePlan, ...]) -> None:
    """Refuse while a table whose ``tenant_id`` becomes required holds a row without one."""
    for table, *_, nullable in plans:
        stub = sa.table(table, sa.column(_TENANT_COLUMN, sa.String))
        unstamped = sa.select(sa.literal(1)).select_from(stub).where(stub.c[_TENANT_COLUMN].is_(None)).limit(1)
        if not nullable and bind.execute(unstamped).first():
            raise RuntimeError(
                f"{table} holds rows without a tenant_id; the tenant_id backfill stamps every row before tenant keys"
            )


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy a batch left behind when its run stopped part way.

    SQLite creates the copy outside the transaction the rest of the batch runs in, so a stopped run can keep it while
    the table itself is rolled back; the name is the one Alembic's batch gives it.
    """
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection, table: str) -> sa.Table:
    """The table as the store holds it, the structure every batch copies from: a batch left to reflect for itself
    drops an unnamed ``CHECK`` constraint from the copy."""
    return sa.Table(table, sa.MetaData(), autoload_with=bind)


def _foreign_key_of(reflected: sa.Table, fk: _ForeignKey) -> sa.ForeignKeyConstraint:
    _, columns, parent, parent_columns = fk
    return next(
        constraint
        for constraint in reflected.foreign_key_constraints
        if tuple(constraint.column_keys) == columns
        and constraint.referred_table.name == parent
        and tuple(element.column.name for element in constraint.elements) == parent_columns
    )


def _rebuild(bind: sa.Connection, plan: _TablePlan, ignored: frozenset[str]) -> None:
    """One batch bringing ``plan[0]`` to ``plan``, leaving the foreign keys named in ``ignored`` as they are.

    The batch copies from the reflected table, so a constraint it keeps leaves as it was, unnamed ones included. An
    unnamed constraint to drop is taken out of that structure rather than dropped by a name it does not have, and an
    unnamed one to restore is put into it; a batch that restores one copies the table on every dialect, since only a
    copy can create a constraint without a name. Only the downgrade restores one, and only on SQLite: there alone does
    the store hold a constraint without a name.
    """
    table = plan[0]
    wanted = _wanted(plan, ignored)
    current = _matched(_shape(bind, table, ignored), wanted)
    if current == wanted:
        return
    _clear_stale_copy(bind, table)
    reflected = _reflected(bind, table)
    (pk_name, pk_columns), uniques, foreign_keys, indexes, nullable = current
    (new_pk_name, new_pk_columns), new_uniques, new_foreign_keys, new_indexes, new_nullable = wanted

    unnamed_restored = (new_pk_name is None and (pk_name, pk_columns) != (None, new_pk_columns)) or any(
        fk[0] is None for fk in new_foreign_keys - foreign_keys
    )
    for fk in foreign_keys - new_foreign_keys:
        if fk[0] is None:
            reflected.constraints.discard(_foreign_key_of(reflected, fk))
    for fk in new_foreign_keys - foreign_keys:
        if fk[0] is None:
            _, columns, parent, parent_columns = fk
            reflected.append_constraint(sa.ForeignKeyConstraint(columns, [f"{parent}.{c}" for c in parent_columns]))
    rekeyed = (pk_name, pk_columns) != (new_pk_name, new_pk_columns)
    if rekeyed:
        for column in reflected.columns:
            column.primary_key = column.name in new_pk_columns
        if new_pk_name is None:
            # The copy keys the table by the flagged columns, unnamed; dropping the old key by name would unflag them.
            reflected.constraints.discard(reflected.primary_key)

    with op.batch_alter_table(table, recreate="always" if unnamed_restored else "auto", copy_from=reflected) as batch:
        for name, *_ in foreign_keys - new_foreign_keys:
            if name is not None:
                batch.drop_constraint(name, type_="foreignkey")
        for name, _ in uniques - new_uniques:
            batch.drop_constraint(_named(name, table), type_="unique")
        for name, _, _ in indexes - new_indexes:
            batch.drop_index(name)
        if rekeyed and new_pk_name is not None:
            if pk_name is not None:
                batch.drop_constraint(pk_name, type_="primary")
            batch.create_primary_key(new_pk_name, list(new_pk_columns))
        if nullable != new_nullable:
            batch.alter_column(_TENANT_COLUMN, existing_type=sa.String(), nullable=new_nullable)
        for name, columns in new_uniques - uniques:
            batch.create_unique_constraint(_named(name, table), list(columns))
        for name, columns, parent, parent_columns in new_foreign_keys - foreign_keys:
            if name is not None:
                batch.create_foreign_key(name, parent, list(columns), list(parent_columns))
        for name, columns, unique in new_indexes - indexes:
            batch.create_index(name, list(columns), unique=unique)


def _named(name: str | None, table: str) -> str:
    if name is None:
        raise RuntimeError(f"{table} carries an unnamed unique constraint the tenant keys cannot address")
    return name


def _link(bind: sa.Connection, plan: _TablePlan, linked: frozenset[str]) -> None:
    """Add the planned foreign keys onto other rebuilt tables that ``plan[0]`` does not carry yet."""
    present = {fk.get("name") for fk in sa.inspect(bind).get_foreign_keys(plan[0])}
    missing = [fk for fk in plan[3] if fk[0] in linked and fk[0] not in present]
    if not missing:
        return
    _clear_stale_copy(bind, plan[0])
    with op.batch_alter_table(plan[0], recreate="auto", copy_from=_reflected(bind, plan[0])) as batch:
        for name, columns, parent, parent_columns in missing:
            batch.create_foreign_key(name, parent, list(columns), list(parent_columns))


def _unlink(bind: sa.Connection, plan: _TablePlan, linked: frozenset[str]) -> None:
    """Drop the foreign keys onto other rebuilt tables that ``plan[0]`` still carries."""
    present = {fk.get("name") for fk in sa.inspect(bind).get_foreign_keys(plan[0])}
    carried = sorted(name for name in linked & present if name in {fk[0] for fk in plan[3]})
    if not carried:
        return
    _clear_stale_copy(bind, plan[0])
    with op.batch_alter_table(plan[0], recreate="auto", copy_from=_reflected(bind, plan[0])) as batch:
        for name in carried:
            batch.drop_constraint(name, type_="foreignkey")


def upgrade() -> None:
    bind = op.get_bind()
    _refuse_unstamped(bind, _PLAN)
    linked = _linked(_PLAN)
    for plan in _children_first(_PLAN):
        _rebuild(bind, plan, linked)
    for plan in _PLAN:
        _link(bind, plan, linked)
    if _CURSOR_TENANT not in _columns(bind, _CURSOR):
        op.add_column(_CURSOR, sa.Column(_CURSOR_TENANT, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    tenants = _tenant_ids(bind)
    if len(tenants) > 1:
        raise RuntimeError(f"delete every tenant but one before downgrading; the store holds {', '.join(tenants)}")
    linked = _linked(_PLAN)
    for plan in _PLAN:
        _unlink(bind, plan, linked)
    for prior in reversed(_children_first(_PRIOR)):
        _rebuild(bind, prior, linked)
    if _CURSOR_TENANT in _columns(bind, _CURSOR):
        with op.batch_alter_table(_CURSOR) as batch:
            batch.drop_column(_CURSOR_TENANT)

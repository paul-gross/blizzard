"""The open-tenant statement builders: which tables of a statement carry the closed-tenant predicate."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.open_tenants import ALL_TENANTS, OPEN_TENANTS

pytestmark = pytest.mark.unit

_CLOSED_CLAUSE = "tenant_deletions"


def _sql(statement: object) -> str:
    return str(statement)


def _predicates(statement: object, table: sa.Table) -> int:
    return _sql(statement).count(f"{table.name}.tenant_id IS NOT NULL")


def test_a_top_level_table_is_confined() -> None:
    assert _predicates(OPEN_TENANTS.select(s.transitions, s.transitions.c.chunk_id), s.transitions) == 1


def test_both_sides_of_an_inner_join_are_confined() -> None:
    join = OPEN_TENANTS.join(
        s.decisions, s.decision_resolutions, s.decisions.c.decision_id == s.decision_resolutions.c.decision_id
    )
    stmt = OPEN_TENANTS.select(join, s.decisions.c.chunk_id)
    assert _predicates(stmt, s.decisions) == 1
    assert "decision_resolutions.tenant_id IS NOT NULL" in _sql(stmt).split("WHERE", 1)[1]


def test_an_outer_join_confines_the_preserved_side_and_keeps_the_right_in_its_on() -> None:
    join = OPEN_TENANTS.join(
        s.questions, s.question_answers, s.questions.c.question_id == s.question_answers.c.question_id, isouter=True
    )
    stmt = _sql(OPEN_TENANTS.select(join, s.questions.c.chunk_id))
    where = stmt.split("WHERE", 1)[1]
    assert "questions.tenant_id IS NOT NULL" in where
    assert "question_answers.tenant_id IS NOT NULL" not in where
    assert "question_answers.tenant_id IS NOT NULL" in stmt.split("WHERE", 1)[0]


def test_a_global_table_passes_through() -> None:
    assert _CLOSED_CLAUSE not in _sql(OPEN_TENANTS.select(s.trace_cursor))


def test_the_chunk_narrowed_form_carries_the_tenant_qualifier() -> None:
    stmt = OPEN_TENANTS.select(s.transitions).where(
        OPEN_TENANTS.within_chunks(s.transitions, s.transitions.c.chunk_id, ["ch_1"])
    )
    sql = _sql(stmt)
    assert "transitions.tenant_id IN (SELECT chunks.tenant_id" in sql
    assert "transitions.chunk_id IN" in sql


def test_a_subquery_built_through_the_builder_is_confined() -> None:
    sub = OPEN_TENANTS.exists(s.chunks, s.chunks.c.chunk_id == s.transitions.c.chunk_id)
    assert "chunks.tenant_id IS NOT NULL" in _sql(OPEN_TENANTS.select(s.transitions).where(sub))


def test_confine_reaches_both_sides_of_a_finished_join() -> None:
    finished = sa.select(s.decisions.c.chunk_id).select_from(
        sa.join(s.decisions, s.decision_resolutions, s.decisions.c.decision_id == s.decision_resolutions.c.decision_id)
    )
    confined = OPEN_TENANTS.confine(finished)
    assert _predicates(confined, s.decisions) == 1
    assert _predicates(confined, s.decision_resolutions) == 1


def test_the_opt_out_applies_no_predicate() -> None:
    assert _CLOSED_CLAUSE not in _sql(ALL_TENANTS.select(s.transitions))

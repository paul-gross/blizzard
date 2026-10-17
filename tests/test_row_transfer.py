"""The machinery a runner transfer's two halves share: tables in the order their reference declarations put them in,
a cycle refused, and a surrogate-key reference rewritten through the keys the destination issued."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from blizzard.runner.store.internal.row_transfer import TransferHandle, in_reference_order, remapped
from blizzard.runner.transfer.rows import DanglingReference

pytestmark = pytest.mark.unit


def _table(metadata: sa.MetaData, name: str, **refers: str) -> sa.Table:
    columns = [sa.Column("id", sa.Integer, primary_key=True, autoincrement=True)]
    columns += [
        sa.Column(column, sa.Integer, nullable=False, info={"refers_to": target}) for column, target in refers.items()
    ]
    return sa.Table(name, metadata, *columns)


def _handle(**issued: dict[int, int]) -> TransferHandle:
    return TransferHandle(runner_id="rn_1", source=None, destination=None, issued=issued)  # type: ignore[arg-type]


def test_a_referred_to_table_comes_before_every_table_naming_it_whatever_their_names() -> None:
    metadata = sa.MetaData()
    a = _table(metadata, "a", b_id="b.id")
    b = _table(metadata, "b", c_id="c.id")
    c = _table(metadata, "c")

    assert [t.name for t in in_reference_order([a, b, c])] == ["c", "b", "a"]


def test_tables_that_refer_to_nothing_are_ordered_by_name() -> None:
    metadata = sa.MetaData()
    tables = [_table(metadata, name) for name in ("z", "m", "a")]

    assert [t.name for t in in_reference_order(tables)] == ["a", "m", "z"]


def test_references_that_form_a_cycle_are_refused() -> None:
    metadata = sa.MetaData()
    tables = [_table(metadata, "a", b_id="b.id"), _table(metadata, "b", a_id="a.id")]

    with pytest.raises(ValueError, match="form a cycle"):
        in_reference_order(tables)


def test_a_reference_to_a_table_outside_the_set_imposes_no_order() -> None:
    metadata = sa.MetaData()
    inside = _table(metadata, "inside", outside_id="outside.id")

    assert [t.name for t in in_reference_order([inside])] == ["inside"]


def test_a_reference_is_rewritten_to_the_key_the_destination_issued() -> None:
    metadata = sa.MetaData()
    child = _table(metadata, "child", parent_id="parent.id")

    assert remapped(child, {"id": 1, "parent_id": 7}, _handle(parent={7: 70}))["parent_id"] == 70


def test_a_required_reference_to_a_row_the_source_no_longer_holds_is_refused() -> None:
    metadata = sa.MetaData()
    child = _table(metadata, "child", parent_id="parent.id")

    with pytest.raises(DanglingReference):
        remapped(child, {"id": 1, "parent_id": 7}, _handle(parent={}))


def test_a_nullable_reference_to_a_row_the_source_no_longer_holds_is_cleared() -> None:
    metadata = sa.MetaData()
    child = sa.Table(
        "child",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("parent_id", sa.Integer, nullable=True, info={"refers_to": "parent.id"}),
    )

    assert remapped(child, {"id": 1, "parent_id": 7}, _handle(parent={}))["parent_id"] is None
    assert remapped(child, {"id": 1, "parent_id": None}, _handle())["parent_id"] is None

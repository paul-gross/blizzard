"""The rehearsal's comparisons over doctored inputs: a lost seeded column, a renamed view field."""

from __future__ import annotations

import pytest

from tests.rehearsal.comparison import project, reflection_drift, response_schema, row_losses, view_mismatches

pytestmark = pytest.mark.unit

_OPENAPI = {
    "paths": {"/api/chunks/{chunk_id}": {"get": {"responses": {"200": {"content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/ChunkDetail"},
    }}}}}}},
    "components": {"schemas": {
        "ChunkDetail": {"properties": {
            "chunk_id": {"type": "string"},
            "status": {"type": "string"},
            "escalation": {"anyOf": [{"$ref": "#/components/schemas/Escalation"}, {"type": "null"}]},
            "history": {"type": "array", "items": {"$ref": "#/components/schemas/Transition"}},
        }},
        "Escalation": {"properties": {"reason": {"type": "string"}}},
        "Transition": {"properties": {"choice_name": {"type": "string"}}},
    }},
}  # fmt: skip


def _projected(view: dict[str, object]) -> dict[str, object]:
    return project(view, response_schema(_OPENAPI, "/api/chunks/{chunk_id}"), _OPENAPI)


def test_a_projection_keeps_only_the_declared_fields_at_every_depth() -> None:
    view = {
        "chunk_id": "ch_1",
        "status": "done",
        "added": 1,
        "escalation": {"reason": "stuck", "added": 2},
        "history": [{"choice_name": "pass", "added": 3}],
    }
    assert _projected(view) == {
        "chunk_id": "ch_1",
        "status": "done",
        "escalation": {"reason": "stuck"},
        "history": [{"choice_name": "pass"}],
    }


def test_a_field_only_the_candidate_adds_is_no_mismatch() -> None:
    before = {"ch_1": _projected({"chunk_id": "ch_1", "status": "done", "escalation": None, "history": []})}
    after = {"ch_1": _projected({"chunk_id": "ch_1", "status": "done", "escalation": None, "history": [], "new": 1})}
    assert view_mismatches(before, after) == []


def test_a_renamed_view_field_names_the_chunk_and_the_field() -> None:
    before = {"ch_1": _projected({"chunk_id": "ch_1", "status": "done", "escalation": None, "history": []})}
    after = {"ch_1": _projected({"chunk_id": "ch_1", "state": "done", "escalation": None, "history": []})}
    assert view_mismatches(before, after) == ["chunk ch_1 field status: 'done' became '<absent>'"]


def test_a_nested_change_names_its_path() -> None:
    before = {"ch_1": {"history": [{"choice_name": "pass"}]}}
    after = {"ch_1": {"history": [{"choice_name": "fail"}]}}
    assert view_mismatches(before, after) == ["chunk ch_1 field history[0] field choice_name: 'pass' became 'fail'"]


def test_a_seeded_column_whose_values_were_lost_names_the_table_row_and_column() -> None:
    before = {"chunks": {"('ch_1',)": {"chunk_id": "'ch_1'", "title": "'seeded'"}}}
    after = {"chunks": {"('ch_1',)": {"chunk_id": "'ch_1'", "title": "None"}}}
    assert row_losses(before, after) == ["table chunks: row ('ch_1',) column title: 'seeded' became None"]


def test_a_dropped_seeded_column_is_a_loss_even_when_the_row_count_holds() -> None:
    before = {"chunks": {"('ch_1',)": {"chunk_id": "'ch_1'", "title": "'seeded'"}}}
    after = {"chunks": {"('ch_1',)": {"chunk_id": "'ch_1'"}}}
    assert row_losses(before, after) == ["table chunks: row ('ch_1',) column title: 'seeded' became <absent>"]


def test_a_lost_row_or_table_is_named_and_rows_added_since_are_not() -> None:
    before = {"chunks": {"('ch_1',)": {"chunk_id": "'ch_1'"}}, "runners": {"('rn_1',)": {"runner_id": "'rn_1'"}}}
    after = {"chunks": {"('ch_2',)": {"chunk_id": "'ch_2'"}}}
    assert row_losses(before, after) == ["table chunks: row ('ch_1',) gone", "table runners: gone"]


def test_reflection_drift_ignores_deprecation_and_names_a_differing_table() -> None:
    column = {"type": "String", "server_default": False}
    committed = {"tables": {
        "a": {"primary_key": [], "columns": {"x": {**column, "deprecated": "0.2"}}},
        "b": {"primary_key": [], "columns": {}},
    }}  # fmt: skip
    reflected = {"tables": {"a": {"primary_key": [], "columns": {"x": {**column, "deprecated": None}}}}}
    assert reflection_drift(reflected, committed) == [
        "table b: reflected <absent> but committed {'primary_key': [], 'columns': {}}"
    ]


def test_reflection_drift_sets_aside_what_postgres_alone_reflects_differently() -> None:
    committed = {"tables": {"q": {"primary_key": ["id"], "columns": {
        "id": {"type": "Integer", "server_default": False},
        "position": {"type": "Float", "server_default": False},
    }}}}  # fmt: skip
    reflected = {"tables": {"q": {"primary_key": ["id"], "columns": {
        "id": {"type": "Integer", "server_default": True},
        "position": {"type": "Double", "server_default": False},
    }}}}  # fmt: skip
    assert reflection_drift(reflected, committed) == []

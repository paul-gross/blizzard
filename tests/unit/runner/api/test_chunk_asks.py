"""``runner.api.chunk_asks.rows``, the pure projection behind ``GET /api/leases/{id}/asks``.

``rows`` — node-label resolution with an id fallback, oldest-first order, and open,
answered, and superseded questions alike."""

from __future__ import annotations

from blizzard.runner.api.chunk_asks import rows
from blizzard.wire.chunk_asks import ChunkAsksSource
from tests.support_chunk_asks_api import DETAIL, question_row


def test_rows_are_oldest_first_and_list_open_and_answered_alike() -> None:
    views = rows(ChunkAsksSource.model_validate(DETAIL))
    assert [r.question_id for r in views] == ["q1", "q2", "q3"]
    assert [r.answered for r in views] == [True, True, False]
    assert views[0].answer == "a" and views[0].answered_by == "paul" and views[0].options == ["a", "b"]
    assert views[2].answer is None


def test_node_label_is_the_name_and_falls_back_to_the_raw_id() -> None:
    views = rows(ChunkAsksSource.model_validate(DETAIL))
    assert views[0].node == "build"
    assert views[1].node == "nd_ghost"


def test_node_name_resolves_from_history_transitions() -> None:
    detail = {
        **DETAIL,
        "current_node_name": None,
        "history": [
            {
                "from_node_id": "nd_build",
                "from_node_name": "build",
                "to_node_id": "nd_review",
                "to_node_name": "review",
                "choice_name": "ready",
                "epoch": 1,
                "recorded_at": "2026-07-21T10:00:00+00:00",
            }
        ],
    }
    assert rows(ChunkAsksSource.model_validate(detail))[0].node == "build"


def test_a_question_without_a_node_has_no_label() -> None:
    detail = {"questions": [question_row("q1", None, "2026-07-21T10:00:00+00:00")]}
    assert rows(ChunkAsksSource.model_validate(detail))[0].node is None


def test_an_answered_row_carries_when_it_was_answered() -> None:
    views = rows(ChunkAsksSource.model_validate(DETAIL))
    assert [r.answered_at for r in views] == ["2026-07-21T10:30:00+00:00", "2026-07-21T11:30:00+00:00", None]


def test_a_question_without_a_node_has_no_label_even_when_the_payload_names_nodes() -> None:
    detail = {**DETAIL, "questions": [question_row("q1", None, "2026-07-21T10:00:00+00:00")]}
    assert rows(ChunkAsksSource.model_validate(detail))[0].node is None


def _transition(
    from_id: str | None, from_name: str | None, to_id: str | None, to_name: str | None
) -> dict[str, object]:
    return {
        "from_node_id": from_id,
        "from_node_name": from_name,
        "to_node_id": to_id,
        "to_node_name": to_name,
        "choice_name": "ready",
        "epoch": 1,
        "recorded_at": "2026-07-21T10:00:00+00:00",
    }


def _migration(
    from_id: str | None, from_name: str | None, landed_id: str | None, landed_name: str | None
) -> dict[str, object]:
    return {
        "from_node_id": from_id,
        "from_node_name": from_name,
        "from_graph_id": "gr_old",
        "to_graph_id": "gr_new",
        "landed_node_id": landed_id,
        "landed_node_name": landed_name,
        "epoch": 1,
        "recorded_at": "2026-07-21T10:00:00+00:00",
    }


def test_node_names_resolve_from_history_and_migrations_mixed_and_drop_half_pairs() -> None:
    detail = {
        "current_node_id": None,
        "current_node_name": None,
        "history": [_transition("nd_plan", "plan", "nd_review", None)],
        "migrations": [
            _migration("nd_old", "old", "nd_landed", "landed"),
            _migration(None, "orphan", "nd_nameless", None),
        ],
        "questions": [
            question_row("q1", "nd_plan", "2026-07-21T10:00:00+00:00"),
            question_row("q2", "nd_old", "2026-07-21T10:01:00+00:00"),
            question_row("q3", "nd_landed", "2026-07-21T10:02:00+00:00"),
            question_row("q4", "nd_review", "2026-07-21T10:03:00+00:00"),
            question_row("q5", "nd_nameless", "2026-07-21T10:04:00+00:00"),
        ],
    }
    views = rows(ChunkAsksSource.model_validate(detail))
    assert [r.node for r in views] == ["plan", "old", "landed", "nd_review", "nd_nameless"]

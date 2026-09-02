"""``GardenRunService`` wired with the real ``GardenRunStore`` (component tier) — a run's
identity minted through ``routine_run.run`` (not hand-rolled), then delivery/escalation
facts seeded directly the ``test_routine_run_service.py`` shape. Proves the three
acceptance shapes together: a run that delivered, one that delivered an empty list, and
one that escalated before delivering anything all appear in the list; a run's delta
reads back added/observed/gone per delivered set; and several sets from one run stay
separately grouped in both the list row and the delta."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.routines import Routine, RunMode
from blizzard.hub.domain.scopes import ScopeSlug
from blizzard.hub.domain.work import Chunk, WorkItemAuthor
from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub

pytestmark = pytest.mark.component

_AUTHOR = WorkItemAuthor.user("usr_1")
_NOW = datetime(2026, 7, 1, tzinfo=UTC)
_SINCE = datetime(2026, 6, 1, tzinfo=UTC)
_UNTIL = datetime(2026, 8, 1, tzinfo=UTC)


def _default_graph(hub: HubHarness) -> Graph:
    return hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )


def _routine(hub: HubHarness, *, name: str = "gardening", scope: str = "blizzard") -> Routine:
    graph = _default_graph(hub)
    return hub.services.routine_authoring.create(
        name=name,
        graph_name=graph.name,
        default_scope_slug=ScopeSlug.parse(scope),
        default_model=None,
        default_effort=None,
    )


def _run(hub: HubHarness, routine: Routine) -> str:
    """Mint a real run through `routine_run.run` — a genuine `work_item_runs` row
    joined through a genuine `chunk_work_refs`/`work_items` pointer, not hand-rolled."""
    result = hub.services.routine_run.run(routine, scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)
    return result.chunk_id


def _seed_delivery(
    hub: HubHarness,
    chunk_id: str,
    finding_set_id: str,
    *,
    artifact_id: str,
    findings: list[dict[str, object]],
    revisions: dict[str, str] | None = None,
    measurement: str | None = None,
    routine_name: str = "gardening",
    produced_at: datetime = _NOW,
) -> None:
    revisions = revisions or {}
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.artifacts).values(
                artifact_id=artifact_id,
                chunk_id=chunk_id,
                node_id="nd_1",
                node_name="deliver",
                epoch=1,
                name="findings",
                kind="asset",
                data=json.dumps(
                    {"scope": "blizzard", "revisions": revisions, "measurement": measurement, "findings": findings}
                ),
                produced_at=produced_at,
            )
        )
        conn.execute(
            insert(s.finding_sets).values(
                finding_set_id=finding_set_id,
                artifact_id=artifact_id,
                chunk_id=chunk_id,
                scope_slug="blizzard",
                routine_name=routine_name,
                revisions=json.dumps(revisions),
                measurement=measurement,
            )
        )


def _seed_add_fact(hub: HubHarness, finding_id: str, *, finding_set_id: str | None, at: datetime = _NOW) -> None:
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.findings).values(
                finding_id=finding_id,
                routine_name="gardening",
                scope_slug="blizzard",
                class_="stale-docstring",
                locus="a.py:1",
                summary="s",
                introduced=None,
                introduced_at=None,
            )
        )
        conn.execute(
            insert(s.finding_facts).values(
                finding_id=finding_id, kind="add", recorded_at=at, finding_set_id=finding_set_id
            )
        )


def _seed_escalation(
    hub: HubHarness, chunk_id: str, *, takeover: str = "resume", wrapped: str = "wrapped-resume", at: datetime = _NOW
) -> None:
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.escalations).values(
                chunk_id=chunk_id,
                epoch=1,
                takeover_command=takeover,
                wrapped_takeover_command=wrapped,
                recorded_at=at,
            )
        )


# --------------------------------------------------------------------------- #
# list_runs


def test_a_delivered_run_appears_with_its_finding_set(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    chunk_id = _run(hub, routine)
    _seed_delivery(hub, chunk_id, "fins_1", artifact_id="art_1", findings=[], revisions={"blizzard": "aaa"})

    (row,) = hub.services.garden_run.list_runs(since=_SINCE, until=_UNTIL)

    assert row.chunk_id == chunk_id
    assert row.routine_name == "gardening"
    assert row.scope_slug == "blizzard"
    assert row.mode == "full"
    assert row.outcome == ChunkStatus.READY
    assert row.escalation is None
    (delivered,) = row.delivered
    assert delivered.finding_set_id == "fins_1"
    assert delivered.revisions == {"blizzard": "aaa"}


def test_a_run_that_delivered_an_empty_list_still_appears_with_its_set(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    chunk_id = _run(hub, routine)
    _seed_delivery(hub, chunk_id, "fins_empty", artifact_id="art_1", findings=[])

    (row,) = hub.services.garden_run.list_runs(since=_SINCE, until=_UNTIL)

    (delivered,) = row.delivered
    assert delivered.finding_set_id == "fins_empty"


def test_an_escalated_run_with_no_delivery_still_appears(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    chunk_id = _run(hub, routine)
    _seed_escalation(hub, chunk_id, takeover="cd /ws && mock-claude-code --resume s1")

    (row,) = hub.services.garden_run.list_runs(since=_SINCE, until=_UNTIL)

    assert row.chunk_id == chunk_id
    assert row.outcome == ChunkStatus.NEEDS_HUMAN
    assert row.delivered == []
    assert row.escalation is not None
    assert row.escalation.takeover_command == "cd /ws && mock-claude-code --resume s1"
    assert row.escalation.wrapped_takeover_command == "wrapped-resume"


def test_every_run_kind_appears_together_in_one_window(tmp_path: Path) -> None:
    """D6: a delivered run, an empty-delivery run, and an escalated run all surface —
    `work_item_runs` is the enumeration source, never `finding_sets` alone."""
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    delivered_chunk = _run(hub, routine)
    _seed_delivery(hub, delivered_chunk, "fins_1", artifact_id="art_1", findings=[])
    empty_chunk = _run(hub, routine)
    _seed_delivery(hub, empty_chunk, "fins_2", artifact_id="art_2", findings=[])
    escalated_chunk = _run(hub, routine)
    _seed_escalation(hub, escalated_chunk)

    rows = hub.services.garden_run.list_runs(since=_SINCE, until=_UNTIL)

    assert {r.chunk_id for r in rows} == {delivered_chunk, empty_chunk, escalated_chunk}
    by_chunk = {r.chunk_id: r for r in rows}
    assert by_chunk[escalated_chunk].outcome == ChunkStatus.NEEDS_HUMAN
    assert by_chunk[escalated_chunk].delivered == []


def test_a_run_outside_the_window_is_excluded(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    _run(hub, routine)

    rows = hub.services.garden_run.list_runs(
        since=datetime(2020, 1, 1, tzinfo=UTC), until=datetime(2020, 2, 1, tzinfo=UTC)
    )

    assert rows == []


def test_several_finding_sets_from_one_run_stay_separately_grouped_in_the_list(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    chunk_id = _run(hub, routine)
    _seed_delivery(hub, chunk_id, "fins_1", artifact_id="art_1", findings=[], revisions={"a": "1"})
    _seed_delivery(hub, chunk_id, "fins_2", artifact_id="art_2", findings=[], revisions={"b": "2"})

    (row,) = hub.services.garden_run.list_runs(since=_SINCE, until=_UNTIL)

    assert {d.finding_set_id for d in row.delivered} == {"fins_1", "fins_2"}


# --------------------------------------------------------------------------- #
# run_delta


def test_run_delta_reads_back_added_observed_and_gone(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    chunk_id = _run(hub, routine)
    findings = [
        {"op": "add", "class": "stale-docstring", "locus": "a.py:1", "summary": "s", "introduced": None},
        {"op": "observed", "id": "fin_seen"},
        {"op": "gone", "id": "fin_missing", "note": "not found this pass"},
    ]
    _seed_delivery(hub, chunk_id, "fins_1", artifact_id="art_1", findings=findings, revisions={"blizzard": "aaa"})
    _seed_add_fact(hub, "fin_new", finding_set_id="fins_1")

    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    delta = hub.services.garden_run.run_delta(chunk)

    assert delta is not None
    assert delta.routine_name == "gardening"
    (set_delta,) = delta.sets
    assert set_delta.finding_set_id == "fins_1"
    (added,) = set_delta.added
    assert added.finding_id == "fin_new"
    assert added.class_ == "stale-docstring"
    assert set_delta.observed == ["fin_seen"]
    (gone,) = set_delta.gone
    assert gone.finding_id == "fin_missing"
    assert gone.note == "not found this pass"


def test_an_add_predating_the_finding_set_link_renders_with_no_matched_id(tmp_path: Path) -> None:
    """A set delivered before `finding_facts.finding_set_id` existed (Phase 1) still
    renders its add from the artifact, but links to no finding id."""
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    chunk_id = _run(hub, routine)
    findings = [{"op": "add", "class": "stale-docstring", "locus": "a.py:1", "summary": "s", "introduced": None}]
    _seed_delivery(hub, chunk_id, "fins_old", artifact_id="art_1", findings=findings)
    # The add's own fact predates the linkage: no `finding_set_id` recorded on it.
    _seed_add_fact(hub, "fin_old", finding_set_id=None)

    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    delta = hub.services.garden_run.run_delta(chunk)

    assert delta is not None
    (set_delta,) = delta.sets
    (added,) = set_delta.added
    assert added.finding_id is None


def test_run_delta_keeps_several_delivered_sets_separately_grouped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    chunk_id = _run(hub, routine)
    first = [{"op": "add", "class": "a", "locus": "a.py:1", "summary": "s1", "introduced": None}]
    second = [{"op": "add", "class": "b", "locus": "b.py:2", "summary": "s2", "introduced": None}]
    _seed_delivery(hub, chunk_id, "fins_1", artifact_id="art_1", findings=first)
    _seed_add_fact(hub, "fin_1", finding_set_id="fins_1")
    _seed_delivery(hub, chunk_id, "fins_2", artifact_id="art_2", findings=second)
    _seed_add_fact(hub, "fin_2", finding_set_id="fins_2")

    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    delta = hub.services.garden_run.run_delta(chunk)

    assert delta is not None
    assert [s.finding_set_id for s in delta.sets] == ["fins_1", "fins_2"]
    assert [s.added[0].finding_id for s in delta.sets] == ["fin_1", "fin_2"]


def test_run_delta_is_none_for_a_chunk_with_no_run_identity(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    ghost = Chunk(chunk_id="ch_ghost", graph_id="gr_1", work_refs=[], minted_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert hub.services.garden_run.run_delta(ghost) is None


def test_a_chunk_that_absorbed_another_runs_work_ref_still_reads_its_own_identity(tmp_path: Path) -> None:
    """`GroupService.group` gives the survivor two `chunk_work_refs` rows once both are
    routine runs (`test_forge_status.py`'s shape) — it must still read back its own
    run, not the merged one's, without raising `MultipleResultsFound`."""
    hub = build_hub(tmp_path)
    survivor_routine = _routine(hub, name="survivor-routine", scope="blizzard")
    merged_routine = _routine(hub, name="merged-routine", scope="blizzard")
    survivor_id = _run(hub, survivor_routine)
    merged_id = _run(hub, merged_routine)

    hub.services.group.group(survivor_id, [merged_id])

    survivor_chunk = hub.services.chunks.record.get(survivor_id)
    assert survivor_chunk is not None
    delta = hub.services.garden_run.run_delta(survivor_chunk)
    assert delta is not None
    assert delta.routine_name == "survivor-routine"

    rows = hub.services.garden_run.list_runs(since=_SINCE, until=_UNTIL)
    by_chunk = {r.chunk_id: r for r in rows}
    assert by_chunk[survivor_id].routine_name == "survivor-routine"

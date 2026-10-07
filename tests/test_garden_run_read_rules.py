"""Garden run read rules (unit tier, by value): the run-list and sweep windows, the
run-row fold, sweep coverage, and baseline assembly — no repository, no clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.ids import Id
from blizzard.foundation.run_mode import RunMode
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.garden.findings.model import FindingSet
from blizzard.hub.domain.garden.runs.baselines import (
    MalformedFindingSetIdError,
    RepoLandings,
    RoutineBaseline,
    newest_swept_first,
    recorded_at_of,
)
from blizzard.hub.domain.garden.runs.history import (
    RunDeliveries,
    RunIdentity,
    RunWindow,
    run_rows,
)
from blizzard.hub.domain.garden.runs.sweeps import SweepFact, SweepWindow, sweep_coverage
from blizzard.hub.domain.garden.runs.window import InvalidWindowError

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 3, 1, 12, tzinfo=UTC)
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


# --- RunWindow ---------------------------------------------------------------


def test_an_unbounded_run_window_is_the_day_ending_now() -> None:
    assert RunWindow.of(None, None, now=_NOW) == RunWindow(since=_NOW - timedelta(hours=24), until=_NOW)


def test_a_run_window_with_only_until_spans_the_day_before_it() -> None:
    until = _NOW - timedelta(days=3)
    assert RunWindow.of(None, until, now=_NOW) == RunWindow(since=until - timedelta(hours=24), until=until)


def test_a_run_window_with_only_since_ends_now() -> None:
    since = _NOW - timedelta(days=3)
    assert RunWindow.of(since, None, now=_NOW) == RunWindow(since=since, until=_NOW)


@pytest.mark.parametrize("until", [_T0, _T0 - timedelta(seconds=1)])
def test_an_empty_or_inverted_run_window_refuses(until: datetime) -> None:
    with pytest.raises(InvalidWindowError) as raised:
        RunWindow.of(_T0, until, now=_NOW)
    assert str(raised.value) == "until must be after since"


def test_a_run_window_of_exactly_the_cap_is_legal() -> None:
    until = _T0 + timedelta(days=RunWindow.MAX_SPAN_DAYS)
    assert RunWindow.of(_T0, until, now=_NOW) == RunWindow(since=_T0, until=until)


def test_a_run_window_past_the_cap_refuses() -> None:
    until = _T0 + timedelta(days=RunWindow.MAX_SPAN_DAYS, seconds=1)
    with pytest.raises(InvalidWindowError) as raised:
        RunWindow.of(_T0, until, now=_NOW)
    assert str(raised.value) == "since/until would span more than 366 days"


# --- run_rows ----------------------------------------------------------------


def _record(chunk_id: str) -> RunDeliveries:
    identity = RunIdentity(
        chunk_id=chunk_id, routine_name="nightly", scope_slug="blizzard", mode=RunMode.FULL, minted_at=_T0
    )
    return RunDeliveries(identity=identity, delivered=[])


def _chunk(chunk_id: str) -> Chunk:
    return Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=_T0)


def test_a_run_whose_chunk_is_gone_is_absent() -> None:
    assert run_rows([_record("ch_gone")], {}, {}) == []


def test_a_run_with_no_facts_reads_as_freshly_minted() -> None:
    (row,) = run_rows([_record("ch_1")], {"ch_1": _chunk("ch_1")}, {})
    assert (row.outcome, row.escalation) == (ChunkStatus.NOT_READY, None)
    assert (row.chunk_id, row.routine_name, row.scope_slug, row.mode, row.minted_at, row.delivered) == (
        "ch_1",
        "nightly",
        "blizzard",
        "full",
        _T0,
        [],
    )


def test_run_rows_keep_the_record_order() -> None:
    chunks = {"ch_1": _chunk("ch_1"), "ch_2": _chunk("ch_2")}
    rows = run_rows([_record("ch_2"), _record("ch_gone"), _record("ch_1")], chunks, {})
    assert [row.chunk_id for row in rows] == ["ch_2", "ch_1"]


# --- SweepWindow and sweep_coverage ------------------------------------------


def test_a_forward_sweep_window_holds_its_edges() -> None:
    assert SweepWindow.of(_T0, _NOW) == SweepWindow(since=_T0, until=_NOW)


@pytest.mark.parametrize("until", [_T0, _T0 - timedelta(days=1)])
def test_an_empty_or_inverted_sweep_window_refuses(until: datetime) -> None:
    with pytest.raises(InvalidWindowError) as raised:
        SweepWindow.of(_T0, until)
    assert str(raised.value) == "until must be after since"


def _fact(slug: str) -> SweepFact:
    return SweepFact(finding_set_id=f"fins_{slug}", scope_slug=slug, produced_at=_T0, revisions={}, measurement=None)


def test_coverage_drops_retired_scopes_and_facts_for_unlinked_ones() -> None:
    live_fact, retired_fact, unlinked_fact = _fact("live"), _fact("cold"), _fact("gone")
    covered, facts = sweep_coverage(
        {"live", "cold", "never"}, {"cold", "gone"}, [live_fact, retired_fact, unlinked_fact]
    )
    assert covered == ["live", "never"]
    assert facts == [live_fact, retired_fact]


# --- baselines ---------------------------------------------------------------


def _finding_set(finding_set_id: str, revisions: dict[str, str]) -> FindingSet:
    return FindingSet(
        finding_set_id=finding_set_id,
        artifact_id="art_1",
        chunk_id="ch_1",
        scope_slug="blizzard",
        routine_name="nightly",
        revisions=revisions,
        measurement=None,
    )


def test_recorded_at_decodes_the_finding_set_ids_mint_instant() -> None:
    assert recorded_at_of(_finding_set(Id.mint_at("fins", _T0).value, {})) == _T0


def test_a_finding_set_id_that_decodes_no_instant_refuses() -> None:
    with pytest.raises(MalformedFindingSetIdError):
        recorded_at_of(_finding_set("not-an-id", {}))


def test_a_baseline_lists_each_repo_in_order_with_its_landings() -> None:
    finding_set = _finding_set("fins_1", {"runner": "bbb", "blizzard": "aaa"})
    baseline = RoutineBaseline.of(finding_set, recorded_at=_T0, landed_since={"blizzard": 2, "runner": 0})
    assert baseline == RoutineBaseline(
        scope_slug="blizzard",
        finding_set_id="fins_1",
        recorded_at=_T0,
        repos=[
            RepoLandings(repo="blizzard", revision="aaa", landed_since=2),
            RepoLandings(repo="runner", revision="bbb", landed_since=0),
        ],
    )


def test_baselines_order_newest_swept_first() -> None:
    older = RoutineBaseline(scope_slug="a", finding_set_id="fins_01", recorded_at=_T0, repos=[])
    newer = RoutineBaseline(scope_slug="b", finding_set_id="fins_02", recorded_at=_T0, repos=[])
    assert newest_swept_first([older, newer]) == [newer, older]

"""The ``events`` rows (unit tier) — facts and derivations built directly, no store."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime
from typing import Any

import pytest

from blizzard.hub.domain.analytics.events import DerivationMarker, DropFact, SegmentProvenance, TranscriptEvent
from blizzard.hub.domain.egress.event_rows import (
    EventDerivation,
    ExportedEventsEntry,
    FilePathPolicy,
    derivation_id,
    derivation_rows,
    dropped_row,
)
from blizzard.hub.domain.egress.rows import step_row
from blizzard.hub.domain.tracing.steps import identify_steps
from blizzard.hub.domain.tracing.summary import summarize_step
from tests import trace_fixtures as fx

pytestmark = pytest.mark.unit

EXPORTED = datetime(2026, 2, 1, tzinfo=UTC)
DERIVED = datetime(2026, 1, 1, 0, 1, 2, 3, tzinfo=UTC)
KEY = b"k3y"
CWD = "/w/r1"
PLANTED = "SENTINEL-do-not-leak"

EVENT_COLUMNS = [
    "record_type", "segment_id", "extractor_version", "derivation_id", "derived_at", "complete", "event_count",
    "dropped_at", "kind", "subject", "tool", "turn_path", "occurrence", "occurred_at", "depth", "agent_type",
    "step_key", "trace_id", "step_started_at", "chunk_id", "epoch", "spawn_generation", "graph_id", "graph_name",
    "node_id", "node_name", "harness_id", "harness_version", "model", "effort", "exported_at",
]  # fmt: skip

RELATIVE = FilePathPolicy("relative", KEY)


def _event(kind: str = "file_read", turn_path: str = "0", occurrence: int = 0, **kw: Any) -> TranscriptEvent:
    fields_: dict[str, Any] = {
        "kind": kind,
        "turn_path": turn_path,
        "occurrence": occurrence,
        "payload": f'{{"prompt": "{PLANTED}"}}',
        "subject": f"{CWD}/src/a.py",
        "tool": "Read",
        "chunk_id": "ch_1",
        "node_id": "g1-build",
        "epoch": 1,
        "spawn_generation": 1,
        "graph_id": f"{PLANTED}-graph",
        "depth": 0,
        "agent_type": None,
        "occurred_at": None,
    }
    return TranscriptEvent(**{**fields_, **kw})


def _derivation(*events: TranscriptEvent, cwd: str | None = CWD, epoch: int = 1) -> EventDerivation:
    return EventDerivation(
        marker=DerivationMarker("seg_1", "blizzard-analytics/5", "fp", DERIVED, len(events), True),
        provenance=SegmentProvenance("claude-code", "2.1", "opus", "high"),
        events=events,
        chunk_id="ch_1",
        epoch=epoch,
        spawn_generation=3,
        spawn_cwd=cwd,
    )


def _rows(*events: TranscriptEvent, paths: FilePathPolicy = RELATIVE, **kw: Any) -> tuple[ExportedEventsEntry, ...]:
    return derivation_rows(fx.scenarios()["runner-step"], _derivation(*events, **kw), paths, EXPORTED)


def _subject(path: str, *, cwd: str | None = CWD, paths: FilePathPolicy = RELATIVE) -> str | None:
    return _rows(_event(subject=path), cwd=cwd, paths=paths)[1].subject


def _hmac(path: str) -> str:
    return FilePathPolicy("hashed", KEY).subject(path, None) or ""


def test_row_fields_are_the_contract_columns_in_order() -> None:
    assert [f.name for f in fields(ExportedEventsEntry)] == EVENT_COLUMNS


def test_a_derivation_gives_its_row_then_one_event_row_each() -> None:
    derivation, first, second = _rows(_event(), _event(turn_path="1", subject="/w/r1/b.py"))
    assert (derivation.record_type, derivation.event_count, derivation.complete) == ("derivation", 2, True)
    assert derivation.derivation_id == derivation_id("seg_1", "blizzard-analytics/5", DERIVED)
    assert (derivation.kind, derivation.subject, derivation.harness_id, derivation.dropped_at) == (None,) * 4
    assert (first.record_type, first.subject, second.subject) == ("event", "src/a.py", "b.py")
    assert (first.harness_id, first.harness_version, first.model, first.effort) == (
        "claude-code",
        "2.1",
        "opus",
        "high",
    )
    assert (first.complete, first.event_count) == (None, None)
    assert {r.derivation_id for r in (derivation, first, second)} == {derivation.derivation_id}


def test_an_empty_derivation_gives_exactly_its_row() -> None:
    (only,) = _rows()
    assert (only.record_type, only.event_count, only.chunk_id, only.epoch) == ("derivation", 0, "ch_1", 1)


def test_an_incomplete_marker_is_carried() -> None:
    derivation = replace(_derivation(), marker=replace(_derivation().marker, complete=False))
    (row,) = derivation_rows(fx.scenarios()["runner-step"], derivation, RELATIVE, EXPORTED)
    assert row.complete is False


def test_a_sidechain_event_at_depth_two() -> None:
    (_, row) = _rows(_event(turn_path="4.1.2", depth=2, agent_type="reviewer"))
    assert (row.turn_path, row.depth, row.agent_type) == ("4.1.2", 2, "reviewer")


def test_two_kinds_at_one_place_differ_only_by_kind() -> None:
    _, a, b = _rows(_event(kind="file_read"), _event(kind="skill_invocation", subject="plan", tool="Skill"))
    assert (a.turn_path, a.occurrence) == (b.turn_path, b.occurrence)
    assert a.kind != b.kind
    assert b.subject == "plan"


def test_every_row_carries_its_step_chunk_and_graph() -> None:
    facts = fx.scenarios()["runner-step"]
    (step,) = [s for s in identify_steps(facts) if s.epoch == 1 and s.kind.value == "runner"]
    for row in _rows(_event()):
        assert (row.step_key, row.step_started_at) == (step.key.text(), step.start)
        assert len(row.trace_id) == 32
        assert (row.chunk_id, row.epoch, row.spawn_generation) == ("ch_1", 1, 3)
        assert (row.graph_id, row.graph_name, row.node_id, row.node_name) == ("g1", "flow", "g1-build", "build")
        assert row.exported_at == EXPORTED


def test_an_event_on_a_migrated_chunk_names_its_step_rows_graph_despite_its_stamp() -> None:
    facts = fx.scenarios()["migrated"]
    (summary,) = [
        s
        for s in (summarize_step(facts, st, identify_steps(facts)) for st in identify_steps(facts))
        if s.epoch == 1 and s.kind.value == "runner"
    ]
    expected = step_row(summary, EXPORTED)
    stamped = _event(graph_id="g2")
    for row in derivation_rows(facts, _derivation(stamped), RELATIVE, EXPORTED):
        assert (row.step_key, row.graph_id, row.graph_name, row.node_id, row.node_name) == (
            expected.step_key,
            expected.graph_id,
            expected.graph_name,
            expected.node_id,
            expected.node_name,
        )


def test_a_drop_gives_one_dropped_row_carrying_its_step() -> None:
    drop = DropFact("seg_1", "ch_1", 1, 3, DERIVED)
    row = dropped_row(fx.scenarios()["runner-step"], drop, EXPORTED)
    derivation = _rows()[0]
    assert (row.record_type, row.dropped_at, row.segment_id, row.spawn_generation) == ("dropped", DERIVED, "seg_1", 3)
    assert (row.step_key, row.trace_id, row.step_started_at) == (
        derivation.step_key,
        derivation.trace_id,
        derivation.step_started_at,
    )
    assert (row.derivation_id, row.derived_at, row.kind, row.subject) == (None,) * 4


def test_a_chunk_mismatch_is_refused_and_a_missing_step_is_not_found() -> None:
    with pytest.raises(ValueError, match="ch_1"):
        derivation_rows(fx.make_facts(chunk_id="ch_other"), _derivation(), RELATIVE, EXPORTED)
    with pytest.raises(LookupError):
        _rows(epoch=7)
    with pytest.raises(LookupError):
        dropped_row(fx.scenarios()["runner-step"], DropFact("s", "ch_1", 9, 1, DERIVED), EXPORTED)


def test_derivation_id_matches_independent_vectors() -> None:
    # printf 'blizzard-derivation/v1/seg_1/blizzard-analytics/5/2026-01-01T00:01:02.000003Z' | sha256sum | cut -c1-32
    assert derivation_id("seg_1", "blizzard-analytics/5", DERIVED) == "c1b58f9074daf091c473d81fb7181863"
    # printf 'blizzard-derivation/v1/seg_2/blizzard-analytics/4/2026-03-04T05:06:07.000000Z' | sha256sum | cut -c1-32
    assert (
        derivation_id("seg_2", "blizzard-analytics/4", datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC))
        == "8e40b7b048b72a1dc918ba2fa79ce28f"
    )


def test_derivation_id_is_stable_and_utc_normalized() -> None:
    from datetime import timedelta, timezone

    shifted = DERIVED.astimezone(timezone(timedelta(hours=5)))
    assert derivation_id("seg_1", "v", shifted) == derivation_id("seg_1", "v", DERIVED)
    assert derivation_id("seg_1", "v", DERIVED) != derivation_id("seg_1", "w", DERIVED)


def test_hashed_is_the_hmac_sha256_of_the_stored_path() -> None:
    # printf '/w/r1/src/a.py' | openssl dgst -sha256 -hmac 'k3y'
    expected = "b7bdac2ff280461fcfbb0d112e70a4f3140595f2e82a67eae22edf57ad5bbaae"
    assert _subject("/w/r1/src/a.py", paths=FilePathPolicy("hashed", KEY)) == expected


def test_relative_paths_inside_the_working_directory() -> None:
    assert _subject("/w/r1/src/a.py") == "src/a.py"
    assert _subject("/w/r1/src/../b.py") == "b.py"
    assert _subject("rel/c.py") == "rel/c.py"
    assert _subject("/w/r1/src/a.py", cwd="/w/r1/") == "src/a.py"


@pytest.mark.parametrize("path", ["/w/r1/../x", "/w/r10/x", "/etc/passwd", "../x", "/w/r1", "/w/r1/"])
def test_relative_paths_outside_or_equal_to_the_working_directory_are_hashed(path: str) -> None:
    assert _subject(path) == _hmac(path)


def test_relative_without_a_working_directory_is_hashed() -> None:
    assert _subject("/w/r1/src/a.py", cwd=None) == _hmac("/w/r1/src/a.py")


def test_absolute_and_omit() -> None:
    assert _subject("/w/r1/src/a.py", paths=FilePathPolicy("absolute")) == "/w/r1/src/a.py"
    assert _subject("/w/r1/src/a.py", paths=FilePathPolicy("omit")) is None


def test_only_file_reads_have_their_subject_rewritten() -> None:
    _, skill, agent = _rows(
        _event(kind="skill_invocation", subject="/w/r1/looks-like-a-path"), _event(kind="agent_spawn", subject="coder")
    )
    assert (skill.subject, agent.subject) == ("/w/r1/looks-like-a-path", "coder")


def test_a_file_read_with_no_subject_stays_null() -> None:
    assert _rows(_event(subject=None))[1].subject is None


@pytest.mark.parametrize("mode", ["relative", "hashed"])
def test_a_keyed_mode_without_a_key_is_refused(mode: Any) -> None:
    with pytest.raises(ValueError, match=mode):
        FilePathPolicy(mode)
    with pytest.raises(ValueError, match=mode):
        FilePathPolicy(mode, b"")


@pytest.mark.parametrize("paths", [RELATIVE, FilePathPolicy("hashed", KEY), FilePathPolicy("omit")])
def test_planted_content_never_reaches_a_row(paths: FilePathPolicy) -> None:
    rows = _rows(_event(subject=f"{CWD}/../outside/a.py"), cwd=CWD, paths=paths)
    for row in rows:
        for value in (getattr(row, f.name) for f in fields(row)):
            assert PLANTED not in str(value)
            if row.record_type == "event" and row.kind == "file_read":
                assert CWD not in str(value)

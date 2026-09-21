"""``harness/internal/opencode_cursor.py::MessagePartCursor.admit`` — unit tier, hermetic:
identity-based admission and D1's own pruning bound. Phase 3's acceptance criterion named the
D1 token bound as unit-tested, but no such test existed until review F5; this file is that
test, plus F23's pairing proof against the pinned compaction corpus."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.runner.harness.internal.opencode_cursor import (
    CursorMark,
    CursorRecord,
    MessagePartCursor,
    MessagePartIdentity,
    records_for_export,
)
from blizzard.runner.harness.internal.opencode_probe import ADMITTED_OPENCODE_VERSIONS
from blizzard.runner.harness.internal.opencode_shapes import parse_session_export

pytestmark = pytest.mark.unit

# Keyed off the admitted set itself (blizzard#438) — there is exactly one member today,
# but this stays correct as the set grows.
_AN_ADMITTED_OPENCODE_VERSION = sorted(ADMITTED_OPENCODE_VERSIONS)[0]
_CORPUS_DIR = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "blizzard"
    / "runner"
    / "harness"
    / "contracts"
    / "opencode"
    / _AN_ADMITTED_OPENCODE_VERSION
)


def _text_record(message_id: str, part_id: str, text: str) -> CursorRecord:
    return CursorRecord.of(message_id, part_id, {"id": part_id, "type": "text", "text": text})


def _compaction_record(message_id: str, part_id: str) -> CursorRecord:
    return CursorRecord.of(message_id, part_id, {"id": part_id, "type": "compaction"})


# --- D1: never prune on absence alone ---


@pytest.mark.unit
def test_admit_never_prunes_a_mark_on_absence_alone() -> None:
    """A stale identity the current tick's export simply omits, with no compaction evidence
    anywhere in it, must stay marked — every export is the WHOLE session, so an absence with
    no compaction proof is indistinguishable from a transient read race (D1)."""
    cursor = MessagePartCursor.start()
    cursor = cursor.admit([_text_record("m1", "p1", "hello")]).cursor

    read = cursor.admit([_text_record("m2", "p2", "world")])

    identities = {mark.identity for mark in read.cursor.marks}
    assert identities == {MessagePartIdentity("m1", "p1"), MessagePartIdentity("m2", "p2")}


@pytest.mark.unit
def test_admit_never_prunes_an_identity_the_export_still_carries() -> None:
    """Pruning only ever drops what is genuinely gone — a live identity the export still
    names stays marked even on a tick that also proves compaction ran."""
    cursor = MessagePartCursor.start()
    cursor = cursor.admit([_text_record("m1", "p1", "hello")]).cursor

    read = cursor.admit([_text_record("m1", "p1", "hello"), _compaction_record("m2", "p-compaction")])

    identities = {mark.identity for mark in read.cursor.marks}
    assert MessagePartIdentity("m1", "p1") in identities


# --- D1: prune only on real compaction evidence ---


@pytest.mark.unit
def test_admit_prunes_a_mark_the_export_no_longer_carries_once_compaction_proves_it() -> None:
    """The one case D1 allows: an identity absent from this tick's export, on a tick whose
    export also carries a `compaction` part — real evidence retained history was pruned, not
    a guess from the mark's own age or the cursor's size."""
    cursor = MessagePartCursor.start()
    cursor = cursor.admit([_text_record("m1", "p1", "hello")]).cursor
    assert MessagePartIdentity("m1", "p1") in {mark.identity for mark in cursor.marks}

    read = cursor.admit([_compaction_record("m2", "p-compaction"), _text_record("m2", "p2", "summary")])

    identities = {mark.identity for mark in read.cursor.marks}
    assert MessagePartIdentity("m1", "p1") not in identities
    assert MessagePartIdentity("m2", "p-compaction") in identities
    assert MessagePartIdentity("m2", "p2") in identities


@pytest.mark.unit
def test_admit_prunes_only_once_the_first_compaction_tick_arrives() -> None:
    """A mark survives every ordinary tick until the FIRST tick whose export actually proves
    compaction — pruning is not retroactive, and not preemptive."""
    cursor = MessagePartCursor.start()
    cursor = cursor.admit([_text_record("m1", "p1", "hello")]).cursor
    cursor = cursor.admit([_text_record("m2", "p2", "still here")]).cursor
    assert MessagePartIdentity("m1", "p1") in {mark.identity for mark in cursor.marks}

    cursor = cursor.admit([_compaction_record("m3", "p-compaction"), _text_record("m3", "p3", "summary")]).cursor

    identities = {mark.identity for mark in cursor.marks}
    assert MessagePartIdentity("m1", "p1") not in identities
    assert MessagePartIdentity("m2", "p2") not in identities


# --- D1: the resulting token is a bounded budget across a long, compacting session ---


@pytest.mark.unit
def test_the_cursor_token_stays_bounded_across_a_long_session_with_periodic_compactions() -> None:
    """ "the resulting token size as a budget" (D1): a session growing through many
    compaction cycles must not grow its token with the total message count ever admitted —
    proportional to one retained window, not the session's whole lifetime."""
    cursor = MessagePartCursor.start()
    per_cycle = 20
    cycles = 50
    for cycle in range(cycles):
        records = [_text_record(f"m{cycle}-{i}", f"p{cycle}-{i}", f"turn {i}") for i in range(per_cycle)]
        records.append(_compaction_record(f"m{cycle}-c", "p-compaction"))
        cursor = cursor.admit(records).cursor

    # Only the LAST cycle's identities remain — everything from an earlier cycle was
    # proven pruned by that cycle's own successor compaction.
    assert len(cursor.marks) == per_cycle + 1
    token_bytes = len(cursor.token.encode("utf-8"))
    assert token_bytes < 8 * 1024, f"cursor token grew to {token_bytes} bytes across {cycles} compaction cycles"


# --- F23: pinned against the real corpus, not a hand-built fixture ---


@pytest.mark.unit
def test_pinned_compaction_fixture_prunes_a_stale_mark_it_no_longer_carries() -> None:
    """The real ``compaction.json`` corpus, not a hand-built stand-in: a mark this export's
    own messages do not carry at all, admitted on a tick this REAL export proves ran
    compaction (it carries a genuine ``compaction`` part), is dropped."""
    export = parse_session_export(json.loads((_CORPUS_DIR / "compaction.json").read_text())["export"])
    stale = MessagePartIdentity("msg_long_gone", "prt_long_gone")
    seeded = MessagePartCursor((CursorMark(stale, "deadbeef"),))

    read = seeded.admit(records_for_export(export))

    identities = {mark.identity for mark in read.cursor.marks}
    assert stale not in identities
    assert MessagePartIdentity("msg_compaction_assistant", "prt_compaction_marker") in identities
    assert MessagePartIdentity("msg_compaction_old", "prt_compaction_old") in identities

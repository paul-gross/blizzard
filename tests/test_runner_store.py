"""Runner-store repository — the facts-only derivations (``bzh:facts-not-status``).

Active = no closure, held = no release, tenure = any unreleased binding. These
assert the SQL derivations the loop relies on, against a real tmp sqlite store.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.harness.fingerprint import PreambleFingerprint
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.store.repository import NewLease
from tests.runner_fakes import make_store

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _mint(store, chunk="ch_1", node="nd_build", node_name="build", epoch=1, lease="lease_1"):  # type: ignore[no-untyped-def]
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id=node,
            node_name=node_name,
            epoch=epoch,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )


@pytest.mark.unit
def test_lease_round_trips_its_own_written_instant(tmp_path):  # type: ignore[no-untyped-def]
    """``created_at`` reads back UTC-aware and equal to what was written (issue #28,
    ``bzh:utc-instants``) — the store column is ``UtcDateTime``-typed, not a plain
    ``DateTime`` that sqlite would hand back naive."""
    store = _store(tmp_path)
    _mint(store)
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    assert lease.created_at == _NOW
    assert lease.created_at.tzinfo is not None


@pytest.mark.unit
def test_minted_lease_is_active_until_closed(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store)
    assert [lease_.lease_id for lease_ in store.list_active_leases()] == ["lease_1"]
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    assert store.list_active_leases() == []
    assert store.active_lease_for_chunk("ch_1") is None


@pytest.mark.component
def test_lease_spans_closure_where_active_lease_does_not(tmp_path):  # type: ignore[no-untyped-def]
    """``lease()`` (issue #29) is the closure-spanning read ``active_lease()`` is *not*
    — a transcript outlives its lease, so the read that serves it must too."""
    store = _store(tmp_path)
    _mint(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)

    assert store.active_lease("lease_1") is None
    closed = store.lease("lease_1")
    assert closed is not None
    assert closed.lease_id == "lease_1"


@pytest.mark.component
def test_lease_returns_none_for_an_unknown_id(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    assert store.lease("no-such-lease") is None


@pytest.mark.component
def test_latest_session_id_returns_most_recent_session_bearing_lease(tmp_path):  # type: ignore[no-untyped-def]
    """Node-entry resume resolution (issue #115): ``node_name=None`` spans every
    node of the chunk, newest-first by mint order."""
    store = _store(tmp_path)
    _mint(store, chunk="ch_1", node="nd_build", node_name="build", lease="lease_1", epoch=1)
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-build-1", spawned_at=_NOW)

    store.record_lease(
        NewLease(
            lease_id="lease_2",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_review",
            node_name="review",
            epoch=2,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW + timedelta(minutes=5),
        )
    )
    store.record_spawn(
        "lease_2", pid=2, process_start_time="2", session_id="sess-review-1", spawned_at=_NOW + timedelta(minutes=5)
    )

    assert store.latest_session_id("ch_1", None) == "sess-review-1"
    assert store.latest_session_id("ch_1", "build") == "sess-build-1"
    assert store.latest_session_id("ch_1", "review") == "sess-review-1"


@pytest.mark.component
def test_latest_session_id_returns_none_when_no_session_or_no_match(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    assert store.latest_session_id("ch_none", None) is None

    _mint(store, chunk="ch_1", node="nd_build", node_name="build", lease="lease_1")
    # Lease minted but never spawned — no session_id yet.
    assert store.latest_session_id("ch_1", None) is None
    assert store.latest_session_id("ch_1", "build") is None
    assert store.latest_session_id("ch_1", "review") is None


@pytest.mark.component
def test_latest_session_id_breaks_created_at_ties_by_lease_id(tmp_path):  # type: ignore[no-untyped-def]
    """``created_at`` is not a total order — tied timestamps must still resolve
    deterministically, by the monotonic ``lease_id`` (bzh:sql-portable)."""
    store = _store(tmp_path)
    _mint(store, chunk="ch_1", node="nd_build", node_name="build", lease="lease_1")
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-1", spawned_at=_NOW)

    store.record_lease(
        NewLease(
            lease_id="lease_2",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=2,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn("lease_2", pid=2, process_start_time="2", session_id="sess-2", spawned_at=_NOW)

    assert store.latest_session_id("ch_1", None) == "sess-2"
    assert store.latest_session_id("ch_1", "build") == "sess-2"


@pytest.mark.component
def test_list_closed_leases_orders_newest_first_and_respects_limit(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store, chunk="ch_1", lease="lease_1")
    _mint(store, chunk="ch_2", lease="lease_2")
    _mint(store, chunk="ch_3", lease="lease_3")
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    store.record_closure(
        lease_id="lease_2",
        chunk_id="ch_2",
        node_id="nd_build",
        reason="failed",
        closed_at=_NOW + timedelta(minutes=5),
    )
    store.record_closure(
        lease_id="lease_3",
        chunk_id="ch_3",
        node_id="nd_build",
        reason="escalated",
        closed_at=_NOW + timedelta(minutes=10),
    )

    closed = store.list_closed_leases(limit=20)
    assert [c.lease.lease_id for c in closed] == ["lease_3", "lease_2", "lease_1"]
    assert closed[0].reason == "escalated"
    assert closed[0].closed_at == _NOW + timedelta(minutes=10)

    limited = store.list_closed_leases(limit=2)
    assert [c.lease.lease_id for c in limited] == ["lease_3", "lease_2"]


@pytest.mark.component
def test_list_closed_leases_excludes_active_leases(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store, chunk="ch_1", lease="lease_1")
    _mint(store, chunk="ch_2", lease="lease_2")
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="reaped", closed_at=_NOW)

    assert [c.lease.lease_id for c in store.list_closed_leases(limit=20)] == ["lease_1"]


@pytest.mark.unit
def test_spawn_facts_populate_pid_and_session(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store)
    store.record_spawn("lease_1", pid=999, process_start_time="12345", session_id="sess-a", spawned_at=_NOW)
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    assert (lease.pid, lease.process_start_time, lease.session_id) == (999, "12345", "sess-a")


@pytest.mark.unit
def test_held_ids_are_bindings_minus_releases(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_binding(chunk_id="ch_2", environment_id="e2", workdir="/ws/e2", bound_at=_NOW)
    assert sorted(store.held_environment_ids()) == ["e1", "e2"]
    assert sorted(store.live_tenure_chunk_ids()) == ["ch_1", "ch_2"]

    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=_NOW)
    assert store.held_environment_ids() == ["e2"]
    assert store.live_tenure_chunk_ids() == ["ch_2"]
    assert store.bindings_for_chunk("ch_1") == []


@pytest.mark.unit
def test_attempt_count_and_latest_epoch_track_retries(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store, lease="lease_1", epoch=1)
    _mint(store, lease="lease_2", epoch=2)
    assert store.attempt_count("ch_1", "nd_build") == 2
    assert store.attempt_count("ch_1", "nd_other") == 0
    assert store.latest_epoch("ch_1") == 2
    assert store.latest_epoch("ch_absent") == 0


@pytest.mark.unit
def test_session_end_fact_is_recorded_and_derived(tmp_path):  # type: ignore[no-untyped-def]
    """A ``session_ends`` row means the worker declared done — startup recovery reads its absence."""
    store = _store(tmp_path)
    _mint(store, lease="lease_1")
    _mint(store, lease="lease_2")
    assert store.session_ended_lease_ids() == set()  # neither has exited

    store.record_session_end(lease_id="lease_1", ended_at=_NOW)
    assert store.session_ended_lease_ids() == {"lease_1"}  # lease_1 declared done; lease_2 did not


@pytest.mark.unit
def test_outbound_buffer_is_fifo_and_ackable(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    s1 = store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW)
    s2 = store.enqueue_outbound(
        kind="completion.submitted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW
    )
    assert s1 < s2
    assert [f.seq for f in store.pending_outbound()] == [s1, s2]
    assert store.pending_outbound()[1].lease_id == "lease_1"
    assert store.pending_submission_lease_ids() == {"lease_1"}
    store.ack_outbound(s1, acked_at=_NOW)
    assert [f.seq for f in store.pending_outbound()] == [s2]


@pytest.mark.unit
def test_workspace_prompt_override_absent_is_none(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Never overridden — the spawn preamble falls back to static config (issue #17).
    store = _store(tmp_path)
    assert store.workspace_prompt_override("ws1") is None


@pytest.mark.unit
def test_workspace_prompt_override_set_then_read_and_upsert(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    store.set_workspace_prompt("ws1", prompt="first", at=_NOW)
    assert store.workspace_prompt_override("ws1") == "first"
    # A second set upserts the single per-workspace row rather than appending.
    store.set_workspace_prompt("ws1", prompt="second", at=_NOW)
    assert store.workspace_prompt_override("ws1") == "second"


@pytest.mark.unit
def test_workspace_prompt_empty_override_is_distinct_from_absent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A present empty override is a deliberate clear-to-table-only — not None (issue #17).
    store = _store(tmp_path)
    store.set_workspace_prompt("ws1", prompt="", at=_NOW)
    assert store.workspace_prompt_override("ws1") == ""


def _sample(kind: UsageKind = "spawn", cost: float | None = 1.5, model: str = "claude-x") -> UsageSample:
    return UsageSample(
        kind=kind,
        model=model,
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=3,
        cache_create_tokens=4,
        cost_usd=cost,
    )


@pytest.mark.unit
def test_lease_generation_counts_spawn_facts(tmp_path):  # type: ignore[no-untyped-def]
    """Generation 1 at the initial spawn, incrementing at each resume (issue #13's own
    tracking, reused as usage's idempotency co-key)."""
    store = _store(tmp_path)
    _mint(store)
    assert store.lease_generation("lease_1") == 0  # minted, not yet spawned
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="s1", spawned_at=_NOW)
    assert store.lease_generation("lease_1") == 1
    store.record_spawn("lease_1", pid=2, process_start_time="2", session_id="s1", spawned_at=_NOW)
    assert store.lease_generation("lease_1") == 2


@pytest.mark.unit
def test_record_usage_lands_fact_and_buffers_outbound(tmp_path):  # type: ignore[no-untyped-def]
    """The atomic local-write + outbound-enqueue pairing (mirrors ``record_local_pause``)."""
    store = _store(tmp_path)
    _mint(store)
    store.record_usage(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        sample=_sample(),
        recorded_at=_NOW,
    )
    totals = store.usage_since(_NOW)
    assert totals.input_tokens == 10
    assert totals.cost_usd == 1.5
    assert totals.cost_partial is False
    pending = store.pending_outbound()
    assert len(pending) == 1
    assert pending[0].kind == "usage.recorded"
    assert pending[0].chunk_id == "ch_1"
    assert pending[0].lease_id == "lease_1"


@pytest.mark.unit
def test_record_usage_is_idempotent_per_lease_generation_kind(tmp_path):  # type: ignore[no-untyped-def]
    """A replay of the exact same invocation (same lease/generation/kind) is a no-op."""
    store = _store(tmp_path)
    _mint(store)
    store.record_usage(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        sample=_sample(),
        recorded_at=_NOW,
    )
    store.record_usage(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        sample=_sample(),
        recorded_at=_NOW,
    )
    totals = store.usage_since(_NOW)
    assert totals.input_tokens == 10  # not doubled
    assert len(store.pending_outbound()) == 1  # not buffered twice


@pytest.mark.unit
def test_record_usage_appends_a_new_row_for_a_new_generation(tmp_path):  # type: ignore[no-untyped-def]
    """A retry/resume within the same lease mints a new generation — a genuinely new row."""
    store = _store(tmp_path)
    _mint(store)
    store.record_usage(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        sample=_sample(kind="spawn"),
        recorded_at=_NOW,
    )
    store.record_usage(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=2,
        sample=_sample(kind="resume"),
        recorded_at=_NOW,
    )
    totals = store.usage_since(_NOW)
    assert totals.input_tokens == 20  # both rows summed
    assert len(store.pending_outbound()) == 2


@pytest.mark.unit
def test_usage_since_flags_partial_on_absent_cost(tmp_path):  # type: ignore[no-untyped-def]
    """A cost-absent row (envelope-less fallback) contributes tokens but flags PARTIAL —
    never fabricated as zero-cost (issue #61's lower-bound + PARTIAL treatment)."""
    store = _store(tmp_path)
    _mint(store)
    store.record_usage(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        sample=_sample(cost=None),
        recorded_at=_NOW,
    )
    totals = store.usage_since(_NOW)
    assert totals.cost_usd == 0.0
    assert totals.cost_partial is True


@pytest.mark.unit
def test_usage_since_excludes_facts_before_the_window(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store)
    earlier = _NOW - timedelta(hours=1)
    store.record_usage(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        sample=_sample(),
        recorded_at=earlier,
    )
    assert store.usage_since(_NOW).input_tokens == 0
    assert store.usage_since(earlier).input_tokens == 10


@pytest.mark.unit
def test_lease_ids_for_chunk_spans_active_and_closed(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store, lease="lease_1", epoch=1)
    _mint(store, lease="lease_2", epoch=2)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="reaped", closed_at=_NOW)
    assert sorted(store.lease_ids_for_chunk("ch_1")) == ["lease_1", "lease_2"]


@pytest.mark.unit
def test_lease_token_hash_absent_for_a_lease_never_minted_one(tmp_path):  # type: ignore[no-untyped-def]
    # issue #113, Phase 1 — never minted here (e.g. a lease from before this revision).
    store = _store(tmp_path)
    _mint(store)
    assert store.lease_token_hash("lease_1") is None


@pytest.mark.unit
def test_lease_token_hash_round_trips_what_was_recorded(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store)
    store.record_lease_token("lease_1", "deadbeef" * 8, _NOW)
    assert store.lease_token_hash("lease_1") == "deadbeef" * 8
    # Scoped per lease — a different lease id has no row of its own.
    assert store.lease_token_hash("lease_2") is None


@pytest.mark.unit
def test_record_lease_token_overwrites_on_re_mint(tmp_path):  # type: ignore[no-untyped-def]
    # A resume re-mints the lease's capability token; the second write replaces the
    # first for the same `lease_id` PK, and the prior token no longer authorizes attach.
    store = _store(tmp_path)
    _mint(store)
    store.record_lease_token("lease_1", "old" * 16, _NOW)
    store.record_lease_token("lease_1", "new" * 16, _NOW)
    assert store.lease_token_hash("lease_1") == "new" * 16


@pytest.mark.unit
def test_attachments_for_lease_is_empty_when_nothing_attached(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store)
    assert store.attachments_for_lease("lease_1") == {}


@pytest.mark.unit
def test_attachments_for_lease_round_trips_what_was_recorded(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store)
    store.record_attachment(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        name="review-findings",
        content="looks good",
        attached_at=_NOW,
    )
    assert store.attachments_for_lease("lease_1") == {"review-findings": "looks good"}


@pytest.mark.unit
def test_attachments_for_lease_is_latest_wins_per_name(tmp_path):  # type: ignore[no-untyped-def]
    """A re-attach of the same name is a correction, not a duplicate — the newest
    row for the pair is what reads back."""
    store = _store(tmp_path)
    _mint(store)
    store.record_attachment(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", epoch=1, name="n", content="first", attached_at=_NOW
    )
    store.record_attachment(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", epoch=1, name="n", content="second", attached_at=_NOW
    )
    assert store.attachments_for_lease("lease_1") == {"n": "second"}


@pytest.mark.unit
def test_attachments_for_lease_is_scoped_per_lease(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store, lease="lease_1")
    _mint(store, lease="lease_2", chunk="ch_2")
    store.record_attachment(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", epoch=1, name="n", content="one", attached_at=_NOW
    )
    store.record_attachment(
        lease_id="lease_2", chunk_id="ch_2", node_id="nd_build", epoch=1, name="n", content="two", attached_at=_NOW
    )
    assert store.attachments_for_lease("lease_1") == {"n": "one"}
    assert store.attachments_for_lease("lease_2") == {"n": "two"}


@pytest.mark.unit
def test_session_preamble_fingerprint_is_none_for_an_unrecorded_session(tmp_path):  # type: ignore[no-untyped-def]
    """The back-compat read (issue #149): a session nothing was ever recorded for reads
    back ``None`` — every pre-existing session inherits this with no data migration."""
    store = _store(tmp_path)
    assert store.session_preamble_fingerprint("sess_never_seen") is None


@pytest.mark.unit
def test_record_session_preamble_round_trips(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    store.record_session_preamble("sess_1", fingerprint=PreambleFingerprint(blizzard="aaa", workspace="bbb"), at=_NOW)
    assert store.session_preamble_fingerprint("sess_1") == PreambleFingerprint(blizzard="aaa", workspace="bbb")


@pytest.mark.unit
def test_session_preamble_fingerprint_is_newest_row_wins(tmp_path):  # type: ignore[no-untyped-def]
    """Append-only, newest-row-is-the-answer (``bzh:facts-not-status``): the second spawn's
    prose is what the third spawn compares against, not the first's."""
    store = _store(tmp_path)
    store.record_session_preamble("sess_1", fingerprint=PreambleFingerprint(blizzard="a1", workspace="w1"), at=_NOW)
    store.record_session_preamble(
        "sess_1", fingerprint=PreambleFingerprint(blizzard="a1", workspace="w2"), at=_NOW + timedelta(minutes=5)
    )
    assert store.session_preamble_fingerprint("sess_1") == PreambleFingerprint(blizzard="a1", workspace="w2")


@pytest.mark.unit
def test_session_preamble_newest_row_wins_at_an_identical_stamp(tmp_path):  # type: ignore[no-untyped-def]
    """Two spawns of one session can share a clock stamp (a fake clock in tests, a coarse
    one in production), so the newest-row read orders on the insert id, never on
    ``recorded_at`` — with an equal stamp, the later insert still wins."""
    store = _store(tmp_path)
    store.record_session_preamble("sess_1", fingerprint=PreambleFingerprint(blizzard="a1", workspace="w1"), at=_NOW)
    store.record_session_preamble("sess_1", fingerprint=PreambleFingerprint(blizzard="a2", workspace="w2"), at=_NOW)
    assert store.session_preamble_fingerprint("sess_1") == PreambleFingerprint(blizzard="a2", workspace="w2")


@pytest.mark.unit
def test_session_preamble_fingerprint_is_scoped_per_session(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    store.record_session_preamble("sess_1", fingerprint=PreambleFingerprint(blizzard="a1", workspace="w1"), at=_NOW)
    store.record_session_preamble("sess_2", fingerprint=PreambleFingerprint(blizzard="a2", workspace="w2"), at=_NOW)
    assert store.session_preamble_fingerprint("sess_1") == PreambleFingerprint(blizzard="a1", workspace="w1")
    assert store.session_preamble_fingerprint("sess_2") == PreambleFingerprint(blizzard="a2", workspace="w2")


# --- transcript segment ledger (issue #246, D1/D2) ---------------------------


@pytest.mark.unit
def test_record_spawn_stamps_a_segment_keyed_by_chunk_node_epoch_generation(tmp_path):  # type: ignore[no-untyped-def]
    """A fresh spawn is generation 1; a resume under the same lease is generation 2 — both
    keyed on the lease's own (chunk, node, epoch), read back from ``lease_context``/``leases``
    inside ``record_spawn``'s own transaction (D1/D2)."""
    store = _store(tmp_path)
    _mint(store, chunk="ch_1", node="nd_build", epoch=1, lease="lease_1")
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)

    open_segments = store.open_transcript_segments()
    assert len(open_segments) == 1
    first = open_segments[0]
    assert (first.chunk_id, first.node_id, first.epoch, first.generation) == ("ch_1", "nd_build", 1, 1)
    assert first.lease_id == "lease_1"
    assert first.session_id == "sess-a"
    assert first.segment_id.startswith("seg_")
    assert (first.cursor, first.shipped_bytes, first.shipped_turns) == (None, 0, 0)
    assert (first.truncated_reason, first.finalized_at) == (None, None)
    assert store.transcript_segment(first.segment_id) == first

    store.record_spawn(
        "lease_1", pid=2, process_start_time="2", session_id="sess-a", spawned_at=_NOW + timedelta(minutes=1)
    )
    segments_by_generation = sorted(store.open_transcript_segments(), key=lambda s: s.generation)
    assert [s.generation for s in segments_by_generation] == [1, 2]
    assert segments_by_generation[1].segment_id != first.segment_id


@pytest.mark.unit
def test_record_spawn_stamps_one_segment_per_lease_at_its_own_epoch(tmp_path):  # type: ignore[no-untyped-def]
    """Two leases (e.g. two retries at fresh epochs) each stamp their own generation-1
    segment — the epoch is part of the key, so the two are never confused."""
    store = _store(tmp_path)
    _mint(store, chunk="ch_1", node="nd_build", epoch=1, lease="lease_1")
    _mint(store, chunk="ch_1", node="nd_build", epoch=2, lease="lease_2")
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    store.record_spawn("lease_2", pid=2, process_start_time="2", session_id="sess-b", spawned_at=_NOW)

    by_lease = {s.lease_id: s for s in store.open_transcript_segments()}
    assert by_lease["lease_1"].epoch == 1
    assert by_lease["lease_2"].epoch == 2
    assert by_lease["lease_1"].generation == by_lease["lease_2"].generation == 1


@pytest.mark.unit
def test_transcript_segment_advance_truncate_and_finalize(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store)
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    segment_id = store.open_transcript_segments()[0].segment_id

    store.advance_transcript_segment(segment_id, cursor="tok-1", shipped_bytes=100, shipped_turns=3)
    advanced = store.transcript_segment(segment_id)
    assert advanced is not None
    assert (advanced.cursor, advanced.shipped_bytes, advanced.shipped_turns) == ("tok-1", 100, 3)

    store.truncate_transcript_segment(segment_id, reason="chunk_budget_exceeded")
    # A second truncation call keeps the first reason rather than overwriting it.
    store.truncate_transcript_segment(segment_id, reason="different_reason")
    assert store.transcript_segment(segment_id).truncated_reason == "chunk_budget_exceeded"  # type: ignore[union-attr]

    assert len(store.open_transcript_segments()) == 1
    store.finalize_transcript_segment(segment_id, finalized_at=_NOW)
    assert store.open_transcript_segments() == []
    assert store.transcript_segment(segment_id).finalized_at == _NOW  # type: ignore[union-attr]


@pytest.mark.unit
def test_transcript_outbound_buffer_is_fifo_ackable_and_its_own_sequence(tmp_path):  # type: ignore[no-untyped-def]
    """The transcript lane's sequence is independent of the fact lane's ``outbound_buffer``
    (D3) — a fact-lane enqueue does not perturb the transcript lane's own numbering."""
    store = _store(tmp_path)
    store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW)

    t1 = store.enqueue_transcript_outbound(segment_id="seg_1", chunk_id="ch_1", payload="{}", created_at=_NOW)
    t2 = store.enqueue_transcript_outbound(segment_id="seg_1", chunk_id="ch_1", payload="{}", created_at=_NOW)
    assert t2 == t1 + 1  # gapless — the fact-lane enqueue above minted no transcript-lane seq
    assert [d.seq for d in store.pending_transcript_outbound()] == [t1, t2]
    assert store.pending_transcript_outbound()[0].segment_id == "seg_1"

    store.ack_transcript_outbound(t1, acked_at=_NOW)
    assert [d.seq for d in store.pending_transcript_outbound()] == [t2]
    # The fact lane's own buffer is untouched by the transcript lane's ack.
    assert len(store.pending_outbound()) == 1

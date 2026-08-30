"""Runner-store repository — the facts-only derivations (``bzh:facts-not-status``).

Active = no closure, held = no release, tenure = any unreleased binding. These
assert the SQL derivations the loop relies on, against a real tmp sqlite store.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from blizzard.foundation.ids import SEGMENT_PREFIX, Id
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.fingerprint import PreambleFingerprint
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.store.schema import transcript_outbound_buffer
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


@pytest.mark.unit
def test_lease_for_open_takeover_resolves_a_closed_reference_lease(tmp_path):  # type: ignore[no-untyped-def]
    """The worker-authorization resolver's second half (issue #291): an open takeover
    names a lease, and this read resolves it regardless of the lease's own closure."""
    store = _store(tmp_path)
    _mint(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)
    assert store.active_lease("lease_1") is None

    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )

    resolved = store.lease_for_open_takeover("lease_1")
    assert resolved is not None
    assert resolved.lease_id == "lease_1"
    assert resolved.node_id == "nd_build"
    assert resolved.epoch == 1


@pytest.mark.unit
def test_lease_for_open_takeover_is_none_with_no_open_takeover(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)
    assert store.lease_for_open_takeover("lease_1") is None


@pytest.mark.unit
def test_lease_for_open_takeover_is_none_once_the_takeover_ends(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    store.record_takeover_end(takeover_id="tko_1", ended_at=_NOW)

    assert store.lease_for_open_takeover("lease_1") is None


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


@pytest.mark.unit
def test_workspace_prompt_clear_removes_the_row_and_reports_what_it_found(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Absence is the only state that resolves back to the configured prompt (issue #344).
    store = _store(tmp_path)
    store.set_workspace_prompt("ws1", prompt="", at=_NOW)
    assert store.clear_workspace_prompt("ws1") is True
    assert store.workspace_prompt_override("ws1") is None
    assert store.clear_workspace_prompt("ws1") is False


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
    """A fresh spawn is generation 1, keyed on the lease's own (chunk, node, epoch), read
    back inside ``record_spawn``'s own transaction (D1/D2). A rotation to a genuinely NEW
    session_id leaves the prior generation open — see the resume test below for the other case."""
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
    assert (first.truncated_reason, first.shipping_stopped_reason, first.finalized_at) == (None, None, None)
    assert store.transcript_segment(first.segment_id) == first

    # A rotation mints a genuinely different session_id — the prior segment stays open,
    # unmerged, until this lease's eventual closure finalizes it (D3).
    store.record_spawn(
        "lease_1", pid=2, process_start_time="2", session_id="sess-b", spawned_at=_NOW + timedelta(minutes=1)
    )
    segments_by_generation = sorted(store.open_transcript_segments(), key=lambda s: s.generation)
    assert [s.generation for s in segments_by_generation] == [1, 2]
    assert segments_by_generation[1].segment_id != first.segment_id
    assert segments_by_generation[1].session_id == "sess-b"
    assert segments_by_generation[1].cursor is None  # a genuinely new session, nothing to carry forward


@pytest.mark.unit
def test_transcript_segments_for_chunk_returns_every_segment_open_or_finalized(tmp_path):  # type: ignore[no-untyped-def]
    """The chunk-scoped index read (D6, runner-node-grouped-transcripts) — unlike
    ``open_transcript_segments``, a finalized segment stays in the result, and a chunk
    this store never held a lease for reads back ``[]`` (D3's ownership exclusion)."""
    store = _store(tmp_path)
    _mint(store, chunk="ch_1", node="nd_build", epoch=1, lease="lease_1")
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    gen1 = store.open_transcript_segments()[0]
    # A same-session resume finalizes gen1 and opens gen2, so the chunk holds one of each.
    store.record_spawn(
        "lease_1", pid=2, process_start_time="2", session_id="sess-a", spawned_at=_NOW + timedelta(minutes=1)
    )
    gen2 = store.open_transcript_segments()[0]

    segments = store.transcript_segments_for_chunk("ch_1")

    assert {s.segment_id for s in segments} == {gen1.segment_id, gen2.segment_id}
    finalized_gen1 = store.transcript_segment(gen1.segment_id)
    assert finalized_gen1 is not None
    assert finalized_gen1.finalized_at is not None
    assert store.transcript_segments_for_chunk("ch_other") == []


@pytest.mark.unit
def test_record_spawn_carries_the_cursor_forward_and_closes_the_prior_segment_on_a_same_session_resume(
    tmp_path,  # type: ignore[no-untyped-def]
):
    """review F3: a pooled resume reuses the SAME session_id under a new generation, which
    would otherwise leave two open segments double-shipping one session. ``record_spawn``
    instead finalizes the outgoing segment and carries its cursor into the new one."""
    store = _store(tmp_path)
    _mint(store, chunk="ch_1", node="nd_build", epoch=1, lease="lease_1")
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    gen1 = store.open_transcript_segments()[0]
    store.record_transcript_deltas(
        segment_id=gen1.segment_id,
        chunk_id="ch_1",
        cursor="tok-1",
        shipped_bytes=100,
        shipped_turns=3,
        normalizer_version="v1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )

    store.record_spawn(
        "lease_1", pid=2, process_start_time="2", session_id="sess-a", spawned_at=_NOW + timedelta(minutes=1)
    )

    open_segments = store.open_transcript_segments()
    assert len(open_segments) == 1  # gen1 closed out at the boundary; only gen2 stays open
    gen2 = open_segments[0]
    assert gen2.generation == 2
    assert gen2.segment_id != gen1.segment_id
    assert gen2.session_id == "sess-a"
    assert gen2.cursor == "tok-1"  # carried forward — gen2 picks up where gen1 left off
    assert (gen2.shipped_bytes, gen2.shipped_turns) == (0, 0)  # its OWN counters start fresh

    finalized_gen1 = store.transcript_segment(gen1.segment_id)
    assert finalized_gen1 is not None
    assert finalized_gen1.finalized_at == _NOW + timedelta(minutes=1)
    assert finalized_gen1.cursor == "tok-1"  # gen1's own row is untouched otherwise

    pending = store.pending_transcript_outbound()
    # gen1's own delta (set up above) plus gen1's own final marker, minted at the resume
    # boundary — nothing for gen2 yet, it has not pumped anything.
    assert {d.segment_id for d in pending} == {gen1.segment_id}
    assert [d.final for d in pending] == [False, True]

    # The chunk-budget sum counts each segment's own contribution exactly once — no double
    # count from carrying the cursor forward, no loss from starting gen2's counters at zero.
    assert store.chunk_transcript_shipped_bytes("ch_1") == 100


@pytest.mark.unit
def test_record_spawn_carries_the_cursor_forward_on_a_cross_lease_resume(tmp_path):  # type: ignore[no-untyped-def]
    """A named session pool resumes across DIFFERENT leases. Goes through the REAL
    production order (review F3): ``record_closure`` finalizes lease_1's segment before
    lease_2 mints, so carry-forward must find it ALREADY finalized."""
    store = _store(tmp_path)
    _mint(store, chunk="ch_1", node="nd_build", epoch=1, lease="lease_1")
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    gen1 = store.open_transcript_segments()[0]
    store.record_transcript_deltas(
        segment_id=gen1.segment_id,
        chunk_id="ch_1",
        cursor="tok-1",
        shipped_bytes=100,
        shipped_turns=3,
        normalizer_version="v1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )
    closed_at = _NOW + timedelta(seconds=30)
    store.record_closure(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=closed_at
    )

    # A different node-step, a different lease — but the SAME session, resumed via the pool.
    _mint(store, chunk="ch_1", node="nd_review", node_name="review", epoch=2, lease="lease_2")
    store.record_spawn(
        "lease_2", pid=2, process_start_time="2", session_id="sess-a", spawned_at=_NOW + timedelta(minutes=1)
    )

    open_segments = store.open_transcript_segments()
    assert len(open_segments) == 1  # lease_1's segment was already closed out by record_closure
    gen2 = open_segments[0]
    assert gen2.lease_id == "lease_2"
    assert gen2.session_id == "sess-a"
    assert gen2.cursor == "tok-1"  # carried forward across the lease boundary, from the ALREADY-finalized segment

    finalized_gen1 = store.transcript_segment(gen1.segment_id)
    assert finalized_gen1 is not None
    assert finalized_gen1.finalized_at == closed_at  # record_closure's own stamp, not record_spawn's later one

    # record_closure already enqueued gen1's one final marker — record_spawn must not enqueue
    # a second one for a segment it didn't finalize itself (TranscriptSegmentFinalizedExactlyOnce).
    finals = [d for d in store.pending_transcript_outbound() if d.segment_id == gen1.segment_id and d.final]
    assert len(finals) == 1


def _pin_next_segment_id_suffixes(monkeypatch, suffixes):  # type: ignore[no-untyped-def]
    """Force the next segment mints' relative id order — a ULID's sub-millisecond half is
    random, so two same-instant ids order as a coin flip and a test reading its expectation
    off that order observes the tie-break only half the time."""
    queued = list(suffixes)
    real = Id.mint_at.__func__  # type: ignore[attr-defined]

    def fake(cls, prefix, at):  # type: ignore[no-untyped-def]
        minted = real(cls, prefix, at)
        return Id(prefix, minted.ulid[:-1] + queued.pop(0)) if prefix == SEGMENT_PREFIX and queued else minted

    monkeypatch.setattr(Id, "mint_at", classmethod(fake))


@pytest.mark.unit
@pytest.mark.parametrize("suffixes", [("A", "Z"), ("Z", "A")], ids=["newer-id-greater", "older-id-greater"])
def test_record_spawn_breaks_a_stamped_at_tie_by_segment_id(tmp_path, monkeypatch, suffixes):  # type: ignore[no-untyped-def]
    """review F5 (`bzh:sql-portable`): two finalized segments sharing an identical
    ``stamped_at`` must resolve deterministically (postgres does not) — run at BOTH id
    orders, so whichever row a tie-break-less scan yields first, one case still fails."""
    _pin_next_segment_id_suffixes(monkeypatch, suffixes)
    store = _store(tmp_path)
    _mint(store, chunk="ch_1", node="nd_build", epoch=1, lease="lease_1")
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    seg1 = store.open_transcript_segments()[0]
    store.record_transcript_deltas(
        segment_id=seg1.segment_id,
        chunk_id="ch_1",
        cursor="tok-1",
        shipped_bytes=1,
        shipped_turns=1,
        normalizer_version="v1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)

    _mint(store, chunk="ch_1", node="nd_build", epoch=2, lease="lease_2")
    # SAME instant as lease_1's own spawn (not advanced) — manufactures the tie.
    store.record_spawn("lease_2", pid=2, process_start_time="2", session_id="sess-a", spawned_at=_NOW)
    seg2 = store.open_transcript_segments()[0]
    store.record_transcript_deltas(
        segment_id=seg2.segment_id,
        chunk_id="ch_1",
        cursor="tok-2",
        shipped_bytes=1,
        shipped_turns=1,
        normalizer_version="v1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )
    store.record_closure(lease_id="lease_2", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    assert seg1.stamped_at == seg2.stamped_at  # the tie the fix must break

    _mint(store, chunk="ch_1", node="nd_build", epoch=3, lease="lease_3")
    store.record_spawn("lease_3", pid=3, process_start_time="3", session_id="sess-a", spawned_at=_NOW)

    gen3 = store.open_transcript_segments()[0]
    winner_cursor = "tok-1" if seg1.segment_id > seg2.segment_id else "tok-2"
    assert gen3.cursor == winner_cursor  # the greater segment_id wins the tie, every time


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
def test_transcript_segment_delta_stop_shipping_and_record_truncated(tmp_path):  # type: ignore[no-untyped-def]
    """The two never-silent reasons (D4) land on two DISTINCT fields (review F1) — a
    segment stays open after ``mark_transcript_record_truncated``, closed off only via
    the production finalize path, ``record_closure``, regardless of either reason."""
    store = _store(tmp_path)
    _mint(store)
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    segment_id = store.open_transcript_segments()[0].segment_id

    store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor="tok-1",
        shipped_bytes=100,
        shipped_turns=3,
        normalizer_version="v1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )
    advanced = store.transcript_segment(segment_id)
    assert advanced is not None
    assert (advanced.cursor, advanced.shipped_bytes, advanced.shipped_turns) == ("tok-1", 100, 3)

    changed_1 = store.mark_transcript_record_truncated(segment_id, reason="record_cap_exceeded", severity=1)
    assert changed_1 is True
    # A repeat of the SAME reason is a no-op (review F14) — no per-tick spam...
    changed_2 = store.mark_transcript_record_truncated(segment_id, reason="record_cap_exceeded", severity=1)
    assert changed_2 is False
    marked = store.transcript_segment(segment_id)
    assert marked is not None
    assert marked.truncated_reason == "record_cap_exceeded"
    assert marked.shipping_stopped_reason is None  # informational only — still open, still pumpable
    assert len(store.open_transcript_segments()) == 1

    # ...but a later, DIFFERENT reason still overwrites the display when it is at least as
    # severe by the caller's explicit ranking, so a milder earlier one never masks a worse one.
    changed_3 = store.mark_transcript_record_truncated(segment_id, reason="record_unshippable", severity=2)
    assert changed_3 is True
    overwritten = store.transcript_segment(segment_id)
    assert overwritten is not None
    assert overwritten.truncated_reason == "record_unshippable"

    # A THIRD, distinct reason still warns even though it is milder than what is displayed:
    # the warning latches per (segment, reason), independent of the display's worst-of rule.
    changed_4 = store.mark_transcript_record_truncated(segment_id, reason="source_read_truncated", severity=0)
    assert changed_4 is True
    milder = store.transcript_segment(segment_id)
    assert milder is not None
    assert milder.truncated_reason == "record_unshippable"  # the milder reason never overwrites the display

    # Re-affirming the reason still on display is a no-op too: a milder reason seen in between
    # must not start it double-warning.
    changed_5 = store.mark_transcript_record_truncated(segment_id, reason="record_unshippable", severity=2)
    assert changed_5 is False

    store.stop_transcript_segment_shipping(segment_id, reason="chunk_budget_exceeded")
    store.stop_transcript_segment_shipping(segment_id, reason="different_reason")
    stopped = store.transcript_segment(segment_id)
    assert stopped is not None
    assert stopped.shipping_stopped_reason == "chunk_budget_exceeded"
    assert stopped.truncated_reason == "record_unshippable"  # untouched by the stop-shipping latch
    assert len(store.open_transcript_segments()) == 1  # stopping shipping does not finalize

    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    assert store.open_transcript_segments() == []
    finalized = store.transcript_segment(segment_id)
    assert finalized is not None
    assert finalized.finalized_at == _NOW  # truncated/stopped does not mean unfinalized


@pytest.mark.unit
@pytest.mark.unit
def test_marking_truncated_survives_a_row_whose_severity_column_was_never_backfilled(tmp_path):  # type: ignore[no-untyped-def]
    """`truncated_reason_severity` arrived after `truncated_reason` and is nullable with no
    backfill, so a row carrying a reason and a NULL severity is reachable and must not raise."""
    store = _store(tmp_path)
    _mint(store)
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    segment_id = store.open_transcript_segments()[0].segment_id
    store.mark_transcript_record_truncated(segment_id, reason="source_read_truncated", severity=0)
    with store._engine.begin() as conn:  # the pre-column shape: a reason, no severity
        conn.execute(
            sa.text("UPDATE transcript_segments SET truncated_reason_severity = NULL WHERE segment_id = :s"),
            {"s": segment_id},
        )

    changed = store.mark_transcript_record_truncated(segment_id, reason="record_unshippable", severity=2)

    assert changed is True
    marked = store.transcript_segment(segment_id)
    assert marked is not None
    assert marked.truncated_reason == "record_unshippable"


def test_transcript_outbound_buffer_is_fifo_ackable_and_its_own_sequence(tmp_path):  # type: ignore[no-untyped-def]
    """The transcript lane's sequence is independent of the fact lane's ``outbound_buffer``
    (D3) — a fact-lane enqueue does not perturb the transcript lane's own numbering."""
    store = _store(tmp_path)
    _mint(store)
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    segment_id = store.open_transcript_segments()[0].segment_id
    store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW)

    (t1,) = store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor="tok-1",
        shipped_bytes=1,
        shipped_turns=1,
        normalizer_version="v1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )
    (t2,) = store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor="tok-2",
        shipped_bytes=2,
        shipped_turns=1,
        normalizer_version="v1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )
    assert t2 == t1 + 1  # gapless — the fact-lane enqueue above minted no transcript-lane seq
    assert [d.seq for d in store.pending_transcript_outbound()] == [t1, t2]
    assert store.pending_transcript_outbound()[0].segment_id == segment_id
    # `limit` bounds the query itself, not just what a caller iterates — issue #246, F5.
    assert [d.seq for d in store.pending_transcript_outbound(limit=1)] == [t1]

    store.ack_transcript_outbound(t1, acked_at=_NOW)
    assert [d.seq for d in store.pending_transcript_outbound()] == [t2]
    # The fact lane's own buffer is untouched by the transcript lane's ack.
    assert len(store.pending_outbound()) == 1

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'runner.db'}")
    with engine.connect() as conn:
        remaining = conn.execute(sa.select(transcript_outbound_buffer.c.seq)).scalars().all()
    # An acked `delta` row is pruned outright, not merely marked — up to the per-record cap each.
    assert list(remaining) == [t2]


@pytest.mark.unit
def test_ack_transcript_outbound_never_reissues_a_pruned_rows_seq(tmp_path):  # type: ignore[no-untyped-def]
    """review F1: a bare SQLite `INTEGER PRIMARY KEY` reuses a deleted row's rowid —
    exactly what pruning the highest-seq acked row sets up. A reissued seq the hub already
    marked applied would read as a replay, silently dropping genuinely new content."""
    store = _store(tmp_path)
    _mint(store)
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    segment_id = store.open_transcript_segments()[0].segment_id
    (t1,) = store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor="tok-1",
        shipped_bytes=1,
        shipped_turns=1,
        normalizer_version="v1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )

    store.ack_transcript_outbound(t1, acked_at=_NOW)  # prunes t1 outright — it was the table's only, and highest, row

    (t2,) = store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor="tok-2",
        shipped_bytes=1,
        shipped_turns=1,
        normalizer_version="v1",
        harness_version=None,
        payloads=["{}"],
        created_at=_NOW,
    )
    assert t2 != t1  # never reissued, even though t1 is now gone and t2 is the new max


@pytest.mark.unit
def test_ack_transcript_outbound_keeps_a_final_marker_row_acked_not_deleted(tmp_path):  # type: ignore[no-untyped-def]
    """Regression: pruning an acked `final` row like a `delta` row breaks
    `TranscriptSegmentFinalizedExactlyOnce`, which counts marker rows still present as the
    receipt a finalized segment's marker landed exactly once."""
    store = _store(tmp_path)
    _mint(store)
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    marker = store.pending_transcript_outbound()
    assert len(marker) == 1
    assert marker[0].final is True

    store.ack_transcript_outbound(marker[0].seq, acked_at=_NOW)

    assert store.pending_transcript_outbound() == []  # no longer pending...
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'runner.db'}")
    with engine.connect() as conn:
        remaining = conn.execute(
            sa.select(transcript_outbound_buffer.c.seq, transcript_outbound_buffer.c.acked_at)
        ).all()
    # ...but the row itself is still there, marked acked — not deleted.
    assert len(remaining) == 1
    assert remaining[0].seq == marker[0].seq
    assert remaining[0].acked_at is not None


@pytest.mark.unit
def test_record_closure_finalizes_every_open_segment_and_marks_it_atomically(tmp_path):  # type: ignore[no-untyped-def]
    """A step's segments are final by step close (issue #246): every open segment for the
    closing lease is finalized and its marker enqueued atomically. Two DIFFERENT
    session_ids (a rotation, not a resume) so both genuinely stay open until closure."""
    store = _store(tmp_path)
    _mint(store, lease="lease_1")
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    store.record_spawn(
        "lease_1", pid=2, process_start_time="2", session_id="sess-b", spawned_at=_NOW + timedelta(minutes=1)
    )
    segment_ids = {s.segment_id for s in store.open_transcript_segments()}
    assert len(segment_ids) == 2  # two generations (two different sessions), both still open

    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)

    assert store.open_transcript_segments() == []
    for segment_id in segment_ids:
        segment = store.transcript_segment(segment_id)
        assert segment is not None
        assert segment.finalized_at == _NOW

    pending = store.pending_transcript_outbound()
    assert {d.segment_id for d in pending} == segment_ids
    assert all(d.final for d in pending)
    # The fact lane's own buffer carries no marker — D3's structural separation.
    assert store.pending_outbound() == []


@pytest.mark.unit
def test_record_closure_is_a_no_op_for_a_lease_with_no_segments(tmp_path):  # type: ignore[no-untyped-def]
    """A closure for a lease that never spawned (or whose segments already finalized)
    enqueues no marker — the existing 30+ callers of ``record_closure`` in tests that
    never touch transcripts are unaffected."""
    store = _store(tmp_path)
    _mint(store, lease="lease_1")
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    assert store.pending_transcript_outbound() == []


@pytest.mark.unit
def test_record_closure_ships_a_final_marker_even_when_no_pump_ever_ran(tmp_path):  # type: ignore[no-untyped-def]
    """Closure finalizes a segment on its normalizer-version sentinel even with no pump
    ever run. The buffered marker row stays minimal (review F8) — this pins the sentinel
    on the ledger row, the one place ``_final_record`` reads it from."""
    store = _store(tmp_path)
    _mint(store, lease="lease_1")
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    segment_id = store.open_transcript_segments()[0].segment_id

    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)

    marker = store.pending_transcript_outbound()
    assert len(marker) == 1
    assert marker[0].final is True
    segment = store.transcript_segment(segment_id)
    assert segment is not None
    assert segment.finalized_at == _NOW
    assert segment.normalizer_version == ""  # the sentinel, never learned from a real read

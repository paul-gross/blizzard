"""Runner-store repository — the facts-only derivations (``bzh:facts-not-status``).

Active = no closure, held = no release, tenure = any unreleased binding. These
assert the SQL derivations the loop relies on, against a real tmp sqlite store.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

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
def test_minted_lease_is_active_until_closed(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _mint(store)
    assert [lease_.lease_id for lease_ in store.list_active_leases()] == ["lease_1"]
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    assert store.list_active_leases() == []
    assert store.active_lease_for_chunk("ch_1") is None


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

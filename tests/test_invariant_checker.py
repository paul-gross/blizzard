"""Component coverage for the facts-level invariant checker (``bzh:invariant-checker``).

Migrates real hub + runner stores, asserts a clean store yields no violations, then
injects each kind of corruption and asserts the matching invariant is named. Every
corruption is injected on a head-migrated store, the only shape the checker is
contracted to read."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import insert

from blizzard.auth_core import Role
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.runtime import init_environment as init_hub
from blizzard.hub.store import schema as hub
from blizzard.hub.store.internal.tenant_teardown_store import TenantTeardownStore
from blizzard.hub.store.scoped import is_tenant_owned
from blizzard.hub.tenancy import TenantId
from blizzard.runner.runtime import init_environment as init_runner
from blizzard.runner.store import schema as runner
from blizzard.tools.invariants import HubInvariants, RunnerInvariants
from tests.runner_fakes import FakeProbe
from tests.support import (
    MIGRATION_CLOCK,
    add_tenant,
    close_tenant,
    default_project,
    default_tenant,
    hub_level_connections,
    hub_scoped,
    hub_scoped_engine,
    migrate_to,
    seed_chunk,
    seed_drainable_history,
    seed_graph,
    seed_membership,
    stamp,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 14, tzinfo=UTC)


def _runner_engine(tmp_path: Path):
    return create_engine_from_url(init_runner(tmp_path / "runner").db_url)


def _hub_engine(tmp_path: Path):
    return create_engine_from_url(init_hub(tmp_path / "hub").db_url)


def test_clean_stores_have_no_violations(tmp_path: Path) -> None:
    assert RunnerInvariants(_runner_engine(tmp_path)).run() == []
    assert HubInvariants(_hub_engine(tmp_path)).run() == []


def test_two_live_leases_for_one_chunk_is_a_violation(tmp_path: Path) -> None:
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for lease_id in ("lease_a", "lease_b"):
            conn.execute(insert(runner.leases).values(lease_id=lease_id, chunk_id="ch_1", epoch=1, created_at=_NOW))
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:one-live-lease-per-chunk" in slugs
    # Closing one lease clears the violation (facts-not-status: a closure is the fact).
    with engine.begin() as conn:
        conn.execute(
            insert(runner.lease_closures).values(
                lease_id="lease_b", chunk_id="ch_1", node_id="nd", reason="transitioned", closed_at=_NOW
            )
        )
    assert RunnerInvariants(engine).run() == []


def test_a_closed_lease_with_an_ambiguous_generation_is_a_violation(tmp_path: Path) -> None:
    """A closed lease's generation launched (``pid`` set) but never identified and never
    marked identity-failed: REAP always resolves one or the other before closing the lease,
    so this shape means that guard was bypassed — a leaked, ownerless process."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.leases).values(lease_id="lease_a", chunk_id="ch_1", epoch=1, created_at=_NOW))
        conn.execute(
            insert(runner.lease_spawns).values(
                lease_id="lease_a", spawned_at=_NOW, pid=4242, process_start_time="start-4242", pgid=4242
            )
        )
        conn.execute(
            insert(runner.lease_closures).values(
                lease_id="lease_a", chunk_id="ch_1", node_id="nd", reason="transitioned", closed_at=_NOW
            )
        )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:no-unowned-live-lease-process" in slugs


def test_a_closed_lease_whose_generation_was_marked_identity_failed_is_not_a_violation(tmp_path: Path) -> None:
    """The other legal close-out of a provisional generation: REAP's own
    ``record_identity_failed`` before ``Attempt.fail`` — not a leak."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.leases).values(lease_id="lease_a", chunk_id="ch_1", epoch=1, created_at=_NOW))
        conn.execute(
            insert(runner.lease_spawns).values(
                lease_id="lease_a",
                spawned_at=_NOW,
                pid=4242,
                process_start_time="start-4242",
                pgid=4242,
                identity_failed_at=_NOW,
            )
        )
        conn.execute(
            insert(runner.lease_closures).values(
                lease_id="lease_a", chunk_id="ch_1", node_id="nd", reason="transitioned", closed_at=_NOW
            )
        )
    assert RunnerInvariants(engine).run() == []


def test_an_active_lease_with_no_live_process_at_its_recorded_pid_is_a_violation(tmp_path: Path) -> None:
    """The checker must probe process liveness for an ACTIVE lease's still-provisional
    generation, not just closed-lease record consistency — a live-looking record whose
    process is gone is exactly a crash-recovery gap. Runs only post-recovery."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(runner.leases).values(
                lease_id="lease_a",
                chunk_id="ch_1",
                epoch=1,
                created_at=_NOW,
                pid=99999,
                process_start_time="start-99999",
            )
        )
    # The default probe is the real `/proc` one — pid 99999 is not this test process, so it
    # reads dead without needing to fake anything.
    slugs = {v.invariant for v in RunnerInvariants(engine).run(after_recovery=True)}
    assert "runner:active-lease-process-is-live" in slugs
    # Not asserted at all before a recovery pass has had its chance to run (the same crash
    # window that leaves it provisional in the first place is not itself a violation).
    assert RunnerInvariants(engine).run(after_recovery=False) == []


def test_an_active_lease_with_a_genuinely_live_process_is_not_a_violation(tmp_path: Path) -> None:
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(runner.leases).values(
                lease_id="lease_a",
                chunk_id="ch_1",
                epoch=1,
                created_at=_NOW,
                pid=4242,
                process_start_time="start-4242",
            )
        )
    probe = FakeProbe(alive={(4242, "start-4242")})
    slugs = {v.invariant for v in RunnerInvariants(engine, process=probe).run(after_recovery=True)}
    assert "runner:active-lease-process-is-live" not in slugs


def test_an_active_lease_whose_generation_already_failed_identity_is_not_a_violation(tmp_path: Path) -> None:
    """The kill already ran and ``identity_failed_at`` is durably recorded, but REAP's next
    pass has not yet closed the lease via ``Attempt.fail`` — pending reconciliation, never a
    leak, whether or not a probe would still find the (already-killed) process alive."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(runner.leases).values(
                lease_id="lease_a",
                chunk_id="ch_1",
                epoch=1,
                created_at=_NOW,
                pid=99999,
                process_start_time="start-99999",
            )
        )
        conn.execute(
            insert(runner.lease_spawns).values(
                lease_id="lease_a",
                spawned_at=_NOW,
                pid=99999,
                process_start_time="start-99999",
                pgid=99999,
                identity_failed_at=_NOW,
            )
        )
    # The default probe is the real `/proc` one — pid 99999 reads dead, which would otherwise
    # trip the check; the recorded identity failure must suppress it regardless.
    slugs = {v.invariant for v in RunnerInvariants(engine).run(after_recovery=True)}
    assert "runner:active-lease-process-is-live" not in slugs


def test_an_active_lease_with_a_genuinely_leaked_provisional_generation_is_still_a_violation(tmp_path: Path) -> None:
    """The real leak the false-positive fix must not paper over: a still-active lease's
    provisional generation with neither a live process nor a recorded identity failure —
    genuinely never going to be reconciled."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(runner.leases).values(
                lease_id="lease_a",
                chunk_id="ch_1",
                epoch=1,
                created_at=_NOW,
                pid=99999,
                process_start_time="start-99999",
            )
        )
        conn.execute(
            insert(runner.lease_spawns).values(
                lease_id="lease_a", spawned_at=_NOW, pid=99999, process_start_time="start-99999", pgid=99999
            )
        )
    slugs = {v.invariant for v in RunnerInvariants(engine).run(after_recovery=True)}
    assert "runner:active-lease-process-is-live" in slugs


def test_an_active_lease_with_two_unidentified_generations_reports_exactly_once(tmp_path: Path) -> None:
    """The provisional join is bounded to the newest open generation per lease — a lease
    with two unidentified ``lease_spawns`` rows at once must still report once, not fan
    out into one violation per matching row."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(runner.leases).values(
                lease_id="lease_a",
                chunk_id="ch_1",
                epoch=1,
                created_at=_NOW,
                pid=99999,
                process_start_time="start-99999",
            )
        )
        for pid in (11111, 99999):  # two open (unidentified) generations on the same lease
            conn.execute(
                insert(runner.lease_spawns).values(
                    lease_id="lease_a", spawned_at=_NOW, pid=pid, process_start_time=f"start-{pid}", pgid=pid
                )
            )
    violations = [
        v
        for v in RunnerInvariants(engine).run(after_recovery=True)
        if v.invariant == "runner:active-lease-process-is-live"
    ]
    assert len(violations) == 1


def test_two_active_leases_claiming_the_same_live_process_is_a_violation(tmp_path: Path) -> None:
    """An ambiguous owner: two distinct active leases (different chunks, so this never
    trips the one-live-lease-per-chunk check) recording the exact same (pid, start_time)
    — at most one durable record can be honest, regardless of provisional status."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for lease_id, chunk_id in (("lease_a", "ch_1"), ("lease_b", "ch_2")):
            conn.execute(
                insert(runner.leases).values(
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    epoch=1,
                    created_at=_NOW,
                    pid=555,
                    process_start_time="start-555",
                )
            )
    probe = FakeProbe(alive={(555, "start-555")})
    slugs = {v.invariant for v in RunnerInvariants(engine, process=probe).run(after_recovery=True)}
    assert "runner:active-lease-process-is-live" in slugs


def test_env_bound_to_two_chunks_is_a_violation(tmp_path: Path) -> None:
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for chunk_id in ("ch_1", "ch_2"):
            conn.execute(
                insert(runner.env_bindings).values(
                    chunk_id=chunk_id, environment_id="env_shared", workdir="/w", bound_at=_NOW
                )
            )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:unique-env-binding" in slugs


def test_gapped_outbound_seq_is_a_violation(tmp_path: Path) -> None:
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for seq in (1, 2, 4):  # 3 is missing — a hole in the FIFO buffer
            conn.execute(
                insert(runner.outbound_buffer).values(
                    seq=seq, kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=_NOW
                )
            )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:gapless-outbound-seq" in slugs


def test_a_pruned_acked_outbound_seq_below_the_pending_floor_is_not_a_violation(tmp_path: Path) -> None:
    """Retention prunes an acked outbound row below the lowest
    still-pending seq — the ordinary case after a prune, not a lost record."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(runner.outbound_buffer).values(
                seq=1, kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=_NOW, acked_at=_NOW
            )
        )
        # seq 2 is deliberately absent — the acked-and-pruned row it once was.
        conn.execute(
            insert(runner.outbound_buffer).values(
                seq=3, kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=_NOW
            )
        )
    assert RunnerInvariants(engine).run() == []


def test_a_hole_at_or_above_the_pending_floor_in_outbound_seq_is_still_a_violation(tmp_path: Path) -> None:
    """The rescoped check still catches a real hole at or above the
    pending floor — a seq that never arrived there is the lost-record case the check exists
    for, whatever retention has already pruned further back."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(runner.outbound_buffer).values(
                seq=1, kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=_NOW, acked_at=_NOW
            )
        )
        for seq in (2, 4):  # 3 is missing at/above the pending floor (2)
            conn.execute(
                insert(runner.outbound_buffer).values(
                    seq=seq, kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=_NOW
                )
            )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:gapless-outbound-seq" in slugs


def test_gapped_transcript_outbound_seq_is_a_violation(tmp_path: Path) -> None:
    """The transcript lane's own gapless-seq check — never `outbound_buffer`'s."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for seq in (1, 2, 4):  # 3 is missing — a hole in the FIFO buffer
            conn.execute(
                insert(runner.transcript_outbound_buffer).values(
                    seq=seq, final=False, segment_id="seg_1", chunk_id="ch_1", payload="{}", created_at=_NOW
                )
            )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:gapless-transcript-outbound-seq" in slugs
    # A fact-lane gap does not trip the transcript lane's own check, and vice versa.
    assert "runner:gapless-outbound-seq" not in slugs


def test_an_acked_and_pruned_transcript_seq_between_two_retained_rows_is_not_a_violation(tmp_path: Path) -> None:
    """An acked non-final row prunes outright, so a healthy drain routinely
    leaves an older retained final marker beside a newer surviving row with a gap between
    — seq 2 here, acked and deleted. The ordinary case, not a lost record."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(runner.transcript_outbound_buffer).values(
                seq=1,
                final=True,
                segment_id="seg_1",
                chunk_id="ch_1",
                payload="{}",
                created_at=_NOW,
                acked_at=_NOW,
            )
        )
        # seq 2 is deliberately absent — the acked-and-pruned non-final row it once was.
        conn.execute(
            insert(runner.transcript_outbound_buffer).values(
                seq=3, final=False, segment_id="seg_2", chunk_id="ch_1", payload="{}", created_at=_NOW
            )
        )
    assert RunnerInvariants(engine).run() == []


def test_a_pending_gap_among_only_unacked_transcript_rows_is_still_a_violation(tmp_path: Path) -> None:
    """The rescoped check still catches a real hole in the pending window — an
    unacked seq that never arrived is exactly the lost-record case the check exists for,
    whether or not an older acked row happens to sit further back."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(runner.transcript_outbound_buffer).values(
                seq=1,
                final=True,
                segment_id="seg_1",
                chunk_id="ch_1",
                payload="{}",
                created_at=_NOW,
                acked_at=_NOW,
            )
        )
        for seq in (2, 4):  # 3 is missing from the still-pending (unacked) window
            conn.execute(
                insert(runner.transcript_outbound_buffer).values(
                    seq=seq, final=False, segment_id="seg_2", chunk_id="ch_1", payload="{}", created_at=_NOW
                )
            )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:gapless-transcript-outbound-seq" in slugs


def _segment_row(**overrides: object) -> dict:
    row: dict = {
        "segment_id": "seg_1",
        "chunk_id": "ch_1",
        "node_id": "nd_build",
        "epoch": 1,
        "generation": 1,
        "lease_id": "lease_1",
        "session_id": "sess_1",
        "harness_id": "claude_code",
        "cursor": None,
        "shipped_bytes": 0,
        "shipped_turns": 0,
        # The source seam's own "never ran" sentinel (`""`) — a real spawn always has SOME
        # normalizer_version to declare, never `None`.
        "normalizer_version": "",
        "harness_version": None,
        "truncated_reason": None,
        "shipping_stopped_reason": None,
        "finalized_at": None,
        "stamped_at": _NOW,
    }
    row.update(overrides)
    return row


def test_finalized_segment_with_no_final_marker_is_a_violation(tmp_path: Path) -> None:
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.transcript_segments).values(**_segment_row(finalized_at=_NOW)))
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:transcript-segment-finalized-exactly-once" in slugs


def test_finalized_segment_with_a_duplicated_final_marker_is_a_violation(tmp_path: Path) -> None:
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.transcript_segments).values(**_segment_row(finalized_at=_NOW)))
        for seq in (1, 2):
            conn.execute(
                insert(runner.transcript_outbound_buffer).values(
                    seq=seq,
                    final=True,
                    segment_id="seg_1",
                    chunk_id="ch_1",
                    payload="{}",
                    created_at=_NOW,
                )
            )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:transcript-segment-finalized-exactly-once" in slugs


def test_finalized_segment_with_exactly_one_final_marker_is_not_a_violation(tmp_path: Path) -> None:
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.transcript_segments).values(**_segment_row(finalized_at=_NOW)))
        conn.execute(
            insert(runner.transcript_outbound_buffer).values(
                seq=1, final=True, segment_id="seg_1", chunk_id="ch_1", payload="{}", created_at=_NOW
            )
        )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:transcript-segment-finalized-exactly-once" not in slugs


def test_pending_non_final_records_do_not_count_as_final_markers(tmp_path: Path) -> None:
    """The check counts `final` rows only: a finalized segment normally still has undrained
    content records beside its one marker, and counting those fires on a healthy store —
    unseen by every tier, which runs `[transcripts] ship` at its default."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.transcript_segments).values(**_segment_row(finalized_at=_NOW)))
        for seq, final in ((1, False), (2, False), (3, True)):
            conn.execute(
                insert(runner.transcript_outbound_buffer).values(
                    seq=seq, final=final, segment_id="seg_1", chunk_id="ch_1", payload="{}", created_at=_NOW
                )
            )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:transcript-segment-finalized-exactly-once" not in slugs


def test_a_finalized_segment_with_only_non_final_records_is_a_violation(tmp_path: Path) -> None:
    """The same filter from the other side: content records are not a close-out."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.transcript_segments).values(**_segment_row(finalized_at=_NOW)))
        conn.execute(
            insert(runner.transcript_outbound_buffer).values(
                seq=1, final=False, segment_id="seg_1", chunk_id="ch_1", payload="{}", created_at=_NOW
            )
        )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:transcript-segment-finalized-exactly-once" in slugs


def test_an_open_segment_needs_no_final_marker(tmp_path: Path) -> None:
    """`finalized_at IS NULL` (still open) is not checked — only a finalized segment must
    have carried its marker."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.transcript_segments).values(**_segment_row(finalized_at=None)))
    assert RunnerInvariants(engine).run() == []


def _usage_row(*, generation: int = 1, kind: str = "spawn") -> dict:
    return {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "node_id": "nd_build",
        "epoch": 1,
        "generation": generation,
        "kind": kind,
        "model": "claude-x",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cache_create_tokens": 0,
        "cost_usd": 0.1,
        "recorded_at": _NOW,
    }


def test_duplicate_usage_attribution_for_one_lease_generation_kind_is_a_violation(tmp_path: Path) -> None:
    """Usage is append-only and idempotent by ``record_usage``'s own check-then-insert, not
    a DB constraint (``bzh:sql-portable``, epic #57) — so two rows for the same
    ``(lease, generation, kind)`` mean that guard was bypassed, and the checker names it."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for _ in range(2):  # two facts for the same (lease_1, generation 1, spawn)
            conn.execute(insert(runner.usage_facts).values(**_usage_row()))
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:usage-attributed-once" in slugs


def test_distinct_generation_or_kind_usage_rows_are_not_a_violation(tmp_path: Path) -> None:
    """A retry/resume within a lease mints a new generation, and a judgement a different
    kind — each a genuinely new row, not a duplicate. The checker stays green over them."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.usage_facts).values(**_usage_row(generation=1, kind="spawn")))
        conn.execute(insert(runner.usage_facts).values(**_usage_row(generation=2, kind="resume")))
        conn.execute(insert(runner.usage_facts).values(**_usage_row(generation=1, kind="judge")))
    assert RunnerInvariants(engine).run() == []


def test_a_closed_leases_open_invocation_boundary_is_a_violation(tmp_path: Path) -> None:
    """(``bzh:open-facts-declare-closure``): once a lease's own closure fact
    is durable, every boundary it opened must be closed too — a still-open one here means
    ``Attempt.close``, the one funnel every closure path shares, was bypassed."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.leases).values(lease_id="lease_a", chunk_id="ch_1", epoch=1, created_at=_NOW))
        conn.execute(
            insert(runner.invocation_boundaries).values(
                lease_id="lease_a",
                chunk_id="ch_1",
                node_id="nd",
                epoch=1,
                generation=1,
                kind="spawn",
                start_position=None,
                opened_at=_NOW,
            )
        )
        conn.execute(
            insert(runner.lease_closures).values(
                lease_id="lease_a", chunk_id="ch_1", node_id="nd", reason="transitioned", closed_at=_NOW
            )
        )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:invocation-boundary-closed-when-lease-closed" in slugs


def test_a_closed_leases_closed_invocation_boundary_is_not_a_violation(tmp_path: Path) -> None:
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.leases).values(lease_id="lease_a", chunk_id="ch_1", epoch=1, created_at=_NOW))
        conn.execute(
            insert(runner.invocation_boundaries).values(
                lease_id="lease_a",
                chunk_id="ch_1",
                node_id="nd",
                epoch=1,
                generation=1,
                kind="spawn",
                start_position=None,
                opened_at=_NOW,
                closed_at=_NOW,
                closed_reason="transitioned",
            )
        )
        conn.execute(
            insert(runner.lease_closures).values(
                lease_id="lease_a", chunk_id="ch_1", node_id="nd", reason="transitioned", closed_at=_NOW
            )
        )
    assert RunnerInvariants(engine).run() == []


def test_an_open_leases_open_invocation_boundary_is_not_a_violation(tmp_path: Path) -> None:
    """A still-ACTIVE lease's boundary is expected to be open — only a CLOSED lease's own
    open boundary is the violation."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.leases).values(lease_id="lease_a", chunk_id="ch_1", epoch=1, created_at=_NOW))
        conn.execute(
            insert(runner.invocation_boundaries).values(
                lease_id="lease_a",
                chunk_id="ch_1",
                node_id="nd",
                epoch=1,
                generation=1,
                kind="spawn",
                start_position=None,
                opened_at=_NOW,
            )
        )
    assert RunnerInvariants(engine).run() == []


def test_a_single_worker_starting_boundary_per_generation_is_not_a_violation(tmp_path: Path) -> None:
    """A ``spawn`` boundary at generation 1 and a ``resume`` boundary at generation 2 are each
    the sole worker-starting boundary of their own generation — the exclusivity invariant is
    scoped per generation, not per lease."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.leases).values(lease_id="lease_a", chunk_id="ch_1", epoch=1, created_at=_NOW))
        for generation, kind in ((1, "spawn"), (2, "resume")):
            conn.execute(
                insert(runner.invocation_boundaries).values(
                    lease_id="lease_a",
                    chunk_id="ch_1",
                    node_id="nd",
                    epoch=1,
                    generation=generation,
                    kind=kind,
                    start_position=None,
                    opened_at=_NOW,
                )
            )
    assert RunnerInvariants(engine).run() == []


def test_two_worker_starting_boundaries_at_one_generation_is_a_violation(tmp_path: Path) -> None:
    """A ``resume`` boundary landing at the same generation an already-open ``nudge``
    boundary owns would corrupt ``_worker_boundary``'s try-in-order lookup silently — the
    checker names it instead."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.leases).values(lease_id="lease_a", chunk_id="ch_1", epoch=1, created_at=_NOW))
        for kind in ("nudge", "resume"):
            conn.execute(
                insert(runner.invocation_boundaries).values(
                    lease_id="lease_a",
                    chunk_id="ch_1",
                    node_id="nd",
                    epoch=1,
                    generation=2,
                    kind=kind,
                    start_position=None,
                    opened_at=_NOW,
                )
            )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:worker-boundary-kind-exclusive-per-generation" in slugs


def test_duplicate_nudge_fact_for_one_lease_epoch_is_a_violation(tmp_path: Path) -> None:
    """`record_nudge_fired` is an insert never an upsert, gated in code not by a DB
    constraint; two rows for the same ``(lease, epoch)`` mean that guard
    was bypassed."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for _ in range(2):
            conn.execute(insert(runner.nudge_facts).values(lease_id="lease_1", epoch=1, nudged_at=_NOW))
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:nudge-at-most-once" in slugs


def test_distinct_lease_or_epoch_nudge_facts_are_not_a_violation(tmp_path: Path) -> None:
    """A different lease, or a later epoch under the same lease id, is a genuinely new
    attempt's own nudge — not a duplicate. The checker stays green over them."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.nudge_facts).values(lease_id="lease_1", epoch=1, nudged_at=_NOW))
        conn.execute(insert(runner.nudge_facts).values(lease_id="lease_1", epoch=2, nudged_at=_NOW))
        conn.execute(insert(runner.nudge_facts).values(lease_id="lease_2", epoch=1, nudged_at=_NOW))
    assert RunnerInvariants(engine).run() == []


def test_duplicate_repo_land_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        for _ in range(2):
            conn.execute(
                insert(hub.delivery_repo_landed).values(
                    stamp(hub.delivery_repo_landed, chunk_id="ch_1", repo="toy-api", commit_hash="abc", landed_at=_NOW)
                )
            )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:per-repo-land-idempotent" in slugs


def test_duplicate_route_seq_across_tables_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.route_created).values(
                stamp(
                    hub.route_created,
                    route_id="rt_1",
                    chunk_id="ch_1",
                    runner_id="r",
                    workspace_id="w",
                    created_at=_NOW,
                    seq=1,
                )
            )
        )
        # Same chunk, same seq as the create above — the exact race #41's tiebreak
        # closed: two route writes both computed seq=1 for chunk ch_1.
        conn.execute(
            insert(hub.route_released).values(stamp(hub.route_released, chunk_id="ch_1", released_at=_NOW, seq=1))
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:route-seq-unique" in slugs


def test_two_live_routes_for_one_chunk_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.route_created).values(
                stamp(
                    hub.route_created,
                    route_id="rt_1",
                    chunk_id="ch_1",
                    runner_id="r_a",
                    workspace_id="w",
                    created_at=_NOW,
                    seq=1,
                )
            )
        )
        # A second `route_created` at a later seq with no `route_released` between the
        # two — the gap `bzh:store-exclusive-write`'s row lock exists to close.
        conn.execute(
            insert(hub.route_created).values(
                stamp(
                    hub.route_created,
                    route_id="rt_2",
                    chunk_id="ch_1",
                    runner_id="r_b",
                    workspace_id="w",
                    created_at=_NOW,
                    seq=2,
                )
            )
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:one-live-route-per-chunk" in slugs


def test_a_released_route_followed_by_a_fresh_claim_is_not_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.route_created).values(
                stamp(
                    hub.route_created,
                    route_id="rt_1",
                    chunk_id="ch_1",
                    runner_id="r_a",
                    workspace_id="w",
                    created_at=_NOW,
                    seq=1,
                )
            )
        )
        conn.execute(
            insert(hub.route_released).values(stamp(hub.route_released, chunk_id="ch_1", released_at=_NOW, seq=2))
        )
        conn.execute(
            insert(hub.route_created).values(
                stamp(
                    hub.route_created,
                    route_id="rt_2",
                    chunk_id="ch_1",
                    runner_id="r_b",
                    workspace_id="w",
                    created_at=_NOW,
                    seq=3,
                )
            )
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:one-live-route-per-chunk" not in slugs


def test_transition_epoch_beyond_latest_lease_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.lease_facts).values(
                stamp(hub.lease_facts, chunk_id="ch_1", epoch=1, runner_id="r", minted_at=_NOW)
            )
        )
        conn.execute(
            insert(hub.transitions).values(
                stamp(
                    hub.transitions,
                    transition_id="tr_1",
                    chunk_id="ch_1",
                    graph_id="gr_1",
                    from_node_id="nd_a",
                    to_node_id="nd_b",
                    choice_name="pass",
                    epoch=2,  # a transition fenced beyond any known lease — a zombie land
                    runner_id="r",
                    recorded_at=_NOW,
                )
            )
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:epoch-consistent-transitions" in slugs


def _seed_owned_chunk(conn) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        insert(hub.chunks).values(stamp(hub.chunks, chunk_id="ch_1", graph_id="gr_1", minted_at=_NOW, model="m"))
    )
    conn.execute(
        insert(hub.epoch_owners).values(
            stamp(hub.epoch_owners, chunk_id="ch_1", epoch=1, runner_id="r_a", recorded_at=_NOW)
        )
    )


def test_owned_epochs_with_matching_leases_are_clean(tmp_path: Path) -> None:
    """``hub:epochs-owned`` holds for a runner lease at its own epoch, a hub lease at a
    hub-owned one, a restart at a hub-owned one, and a reservation with no lease yet."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        _seed_owned_chunk(conn)
        conn.execute(
            insert(hub.lease_facts).values(
                stamp(hub.lease_facts, chunk_id="ch_1", epoch=1, runner_id="r_a", minted_at=_NOW)
            )
        )
        conn.execute(
            insert(hub.epoch_owners).values(
                stamp(hub.epoch_owners, chunk_id="ch_1", epoch=2, runner_id=None, recorded_at=_NOW)
            )
        )
        conn.execute(
            insert(hub.lease_facts).values(
                stamp(hub.lease_facts, chunk_id="ch_1", epoch=2, runner_id="hub", minted_at=_NOW)
            )
        )
        conn.execute(
            insert(hub.epoch_owners).values(
                stamp(hub.epoch_owners, chunk_id="ch_1", epoch=3, runner_id=None, recorded_at=_NOW)
            )
        )
        conn.execute(
            insert(hub.chunk_restarts).values(
                stamp(
                    hub.chunk_restarts,
                    chunk_id="ch_1",
                    graph_id="gr_1",
                    to_node_id="nd_a",
                    epoch=3,
                    restarted_by="op",
                    recorded_at=_NOW,
                )
            )
        )
        conn.execute(
            insert(hub.epoch_owners).values(
                stamp(hub.epoch_owners, chunk_id="ch_1", epoch=4, runner_id="r_b", recorded_at=_NOW)
            )
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:epochs-owned" not in slugs


def test_a_lease_at_an_unowned_epoch_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        _seed_owned_chunk(conn)
        conn.execute(
            insert(hub.lease_facts).values(
                stamp(hub.lease_facts, chunk_id="ch_1", epoch=2, runner_id="r_a", minted_at=_NOW)
            )
        )
    assert [v.detail for v in HubInvariants(engine).run() if v.invariant == "hub:epochs-owned"] == [
        "chunk ch_1 epoch 2 has no owner"
    ]


def test_a_restart_at_an_unowned_epoch_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        _seed_owned_chunk(conn)
        conn.execute(
            insert(hub.chunk_restarts).values(
                stamp(
                    hub.chunk_restarts,
                    chunk_id="ch_1",
                    graph_id="gr_1",
                    to_node_id="nd_a",
                    epoch=2,
                    restarted_by="op",
                    recorded_at=_NOW,
                )
            )
        )
    assert [v.detail for v in HubInvariants(engine).run() if v.invariant == "hub:epochs-owned"] == [
        "chunk ch_1 epoch 2 has no owner"
    ]


def test_a_runner_lease_level_with_another_owners_epoch_is_a_violation(tmp_path: Path) -> None:
    """A displaced attempt's mint landing at an epoch another runner — or the hub — owns."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        _seed_owned_chunk(conn)
        conn.execute(
            insert(hub.lease_facts).values(
                stamp(hub.lease_facts, chunk_id="ch_1", epoch=1, runner_id="r_b", minted_at=_NOW)
            )
        )
        conn.execute(
            insert(hub.epoch_owners).values(
                stamp(hub.epoch_owners, chunk_id="ch_1", epoch=2, runner_id=None, recorded_at=_NOW)
            )
        )
        conn.execute(
            insert(hub.lease_facts).values(
                stamp(hub.lease_facts, chunk_id="ch_1", epoch=2, runner_id="r_a", minted_at=_NOW)
            )
        )
    assert [v.detail for v in HubInvariants(engine).run() if v.invariant == "hub:epochs-owned"] == [
        "chunk ch_1 epoch 1 taken by r_b but owned by r_a",
        "chunk ch_1 epoch 2 taken by r_a but owned by the hub",
    ]


def test_landed_fact_without_terminal_transition_is_a_two_state_violation(tmp_path: Path) -> None:
    """``hub:merge-queue-single-state`` — a whole-chunk ``delivery.landed`` fact paired
    with a non-terminal newest transition reads as both landed and mid-flight;
    defense-in-depth, since a real store never writes this shape."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(stamp(hub.chunks, chunk_id="ch_1", graph_id="gr_1", minted_at=_NOW, model="m"))
        )
        conn.execute(
            insert(hub.lease_facts).values(
                stamp(hub.lease_facts, chunk_id="ch_1", epoch=2, runner_id="hub", minted_at=_NOW)
            )
        )
        # Newest transition targets a post-merge worker node, NOT the reserved terminal.
        conn.execute(
            insert(hub.transitions).values(
                stamp(
                    hub.transitions,
                    transition_id="tr_1",
                    chunk_id="ch_1",
                    graph_id="gr_1",
                    from_node_id="nd_deliver",
                    to_node_id="nd_verify",
                    choice_name="landed",
                    epoch=2,
                    runner_id="hub",
                    recorded_at=_NOW,
                )
            )
        )
        conn.execute(insert(hub.delivery_landed).values(stamp(hub.delivery_landed, chunk_id="ch_1", landed_at=_NOW)))
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:merge-queue-single-state" in slugs


def test_merged_into_post_merge_node_is_not_a_violation(tmp_path: Path) -> None:
    """#63's legal shape: a chunk merged into a post-merge node is clean, never flagged —
    it carries per-repo ``delivery.repo_landed`` facts but no whole-chunk
    ``delivery.landed`` fact, so it derives its live status rather than DONE."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(stamp(hub.chunks, chunk_id="ch_1", graph_id="gr_1", minted_at=_NOW, model="m"))
        )
        conn.execute(
            insert(hub.lease_facts).values(
                stamp(hub.lease_facts, chunk_id="ch_1", epoch=2, runner_id="hub", minted_at=_NOW)
            )
        )
        conn.execute(
            insert(hub.epoch_owners).values(
                stamp(hub.epoch_owners, chunk_id="ch_1", epoch=2, runner_id=None, recorded_at=_NOW)
            )
        )
        conn.execute(
            insert(hub.transitions).values(
                stamp(
                    hub.transitions,
                    transition_id="tr_1",
                    chunk_id="ch_1",
                    graph_id="gr_1",
                    from_node_id="nd_deliver",
                    to_node_id="nd_verify",
                    choice_name="landed",
                    epoch=2,
                    runner_id="hub",
                    recorded_at=_NOW,
                )
            )
        )
        conn.execute(
            insert(hub.delivery_repo_landed).values(
                stamp(hub.delivery_repo_landed, chunk_id="ch_1", repo="toy-api", commit_hash="abc", landed_at=_NOW)
            )
        )
    assert HubInvariants(engine).run() == []


def test_two_open_pause_parks_on_one_lease_is_a_violation(tmp_path: Path) -> None:
    """``runner:one-open-pause-park-per-lease`` — PULL's park guard is the only thing
    keeping a standing pause to a single open park (plan §7)."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for _ in range(2):  # the same standing pause parked twice — the dropped-guard shape
            conn.execute(insert(runner.pause_parks).values(lease_id="lease_a", chunk_id="ch_1", parked_at=_NOW))
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:one-open-pause-park-per-lease" in slugs

    # Resuming the lease closes both parks (the resume is at/after each) — no violation.
    with engine.begin() as conn:
        conn.execute(insert(runner.pause_park_resumes).values(lease_id="lease_a", resumed_at=_NOW))
    assert RunnerInvariants(engine).run() == []


def test_a_repause_on_one_lease_is_not_a_violation(tmp_path: Path) -> None:
    """Pause -> resume -> pause again on one lease is legitimate: only the newest park is
    open, so the invariant must not fire. Guards against a checker written as a naive
    'at most one pause_parks row per lease' count, which would forbid a re-pause."""
    engine = _runner_engine(tmp_path)
    t1 = datetime(2026, 7, 14, 1, tzinfo=UTC)
    t2 = datetime(2026, 7, 14, 2, tzinfo=UTC)
    with engine.begin() as conn:
        conn.execute(insert(runner.pause_parks).values(lease_id="lease_a", chunk_id="ch_1", parked_at=_NOW))
        conn.execute(insert(runner.pause_park_resumes).values(lease_id="lease_a", resumed_at=t1))
        conn.execute(insert(runner.pause_parks).values(lease_id="lease_a", chunk_id="ch_1", parked_at=t2))
    assert RunnerInvariants(engine).run() == []


def test_a_closed_leases_double_park_is_not_an_open_pause_park(tmp_path: Path) -> None:
    """The checker's mirror agrees with the store predicate: a closed lease's parks are
    closed with it, so two parks on it are no violation."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for _ in range(2):
            conn.execute(insert(runner.pause_parks).values(lease_id="lease_a", chunk_id="ch_1", parked_at=_NOW))
        conn.execute(
            insert(runner.lease_closures).values(
                lease_id="lease_a", chunk_id="ch_1", node_id="nd", reason="terminal", closed_at=_NOW
            )
        )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:one-open-pause-park-per-lease" not in slugs


def test_open_pause_parks_on_different_leases_are_not_a_violation(tmp_path: Path) -> None:
    """The invariant is per-lease: two chunks paused at once is the normal world."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.pause_parks).values(lease_id="lease_a", chunk_id="ch_1", parked_at=_NOW))
        conn.execute(insert(runner.pause_parks).values(lease_id="lease_b", chunk_id="ch_2", parked_at=_NOW))
    assert RunnerInvariants(engine).run() == []


def test_a_double_park_after_a_repause_is_still_a_violation(tmp_path: Path) -> None:
    """The ``>=`` correlation in the checker's mirror is load-bearing: only a resume at
    or after a given park closes *that* park, not any resume on the lease."""
    engine = _runner_engine(tmp_path)
    t1 = datetime(2026, 7, 14, 1, tzinfo=UTC)
    t2 = datetime(2026, 7, 14, 2, tzinfo=UTC)
    with engine.begin() as conn:
        conn.execute(insert(runner.pause_parks).values(lease_id="lease_a", chunk_id="ch_1", parked_at=_NOW))
        conn.execute(insert(runner.pause_park_resumes).values(lease_id="lease_a", resumed_at=t1))
        # Re-paused, then parked again by a tick that lost its guard — two parks open
        # above the resume, on a lease that *does* carry a resume fact.
        conn.execute(insert(runner.pause_parks).values(lease_id="lease_a", chunk_id="ch_1", parked_at=t2))
        conn.execute(insert(runner.pause_parks).values(lease_id="lease_a", chunk_id="ch_1", parked_at=t2))
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:one-open-pause-park-per-lease" in slugs


def _seed_migration(
    conn,
    *,
    to_graph: str,
    model_after: str | None,
    pin_graph: str,
    pin_default_model: list[str],
    release_route: bool = True,
    landed_executor: str | None = None,
    source: str | None = None,
) -> None:
    """A chunk pinned to (pin_graph, pin_default_model) with one migration targeting
    to_graph (#90). ``release_route=False`` seeds a torn write where the migration fact
    landed but the route release did not. ``landed_executor`` seeds a ``graph_nodes``
    row for the landing node; a ``"hub"`` landing and a ``"restart"``
    ``source`` (#371) are exempt from the route-released check."""
    # `chunks.model` is retained-and-unread since #144; the re-pin the invariant checks
    # lands in `default_model`, so that is what a seeded pin has to carry.
    conn.execute(
        insert(hub.chunks).values(
            stamp(
                hub.chunks,
                chunk_id="ch_1",
                graph_id=pin_graph,
                minted_at=_NOW,
                model="unread-since-144",
                default_model=json.dumps(pin_default_model) if pin_default_model else None,
            )
        )
    )
    if landed_executor is not None:
        conn.execute(
            insert(hub.graph_nodes).values(
                stamp(
                    hub.graph_nodes,
                    node_id="nd_landed",
                    graph_id=to_graph,
                    name="build",
                    executor=landed_executor,
                    session="fresh",
                    judged_by="worker",
                )
            )
        )
    conn.execute(
        insert(hub.chunk_migrations).values(
            stamp(
                hub.chunk_migrations,
                migration_id="mg_1",
                chunk_id="ch_1",
                from_node_id="nd_from",
                from_graph_id="gr_src",
                to_graph_id=to_graph,
                landed_node_id="nd_landed",
                choice_name="migrate",
                model_after=model_after,
                epoch=1,
                recorded_at=_NOW,
                source=source,
            )
        )
    )
    if release_route:
        conn.execute(
            insert(hub.route_released).values(stamp(hub.route_released, chunk_id="ch_1", released_at=_NOW, seq=1))
        )


def test_a_consistent_migration_is_not_a_violation(tmp_path: Path) -> None:
    """The atomic re-pin landed with the fact: the chunk's pin matches its migration (#90)."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        _seed_migration(
            conn, to_graph="gr_triage", model_after="claude-x", pin_graph="gr_triage", pin_default_model=["claude-x"]
        )
    assert HubInvariants(engine).run() == []


def test_a_migration_without_its_graph_repin_is_a_violation(tmp_path: Path) -> None:
    """``hub:migration-pin-consistent`` — a migration fact whose graph re-pin never landed:
    the half-write a kill -9 in the ``migrate.`` window must never leave (#90)."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        _seed_migration(conn, to_graph="gr_triage", model_after=None, pin_graph="gr_src", pin_default_model=["m"])
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:migration-pin-consistent" in slugs


def test_a_migration_without_its_model_repin_is_a_violation(tmp_path: Path) -> None:
    """The graph pin landed but the model re-pin did not — still a torn migration write (#90)."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        _seed_migration(
            conn, to_graph="gr_triage", model_after="claude-x", pin_graph="gr_triage", pin_default_model=["stale"]
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:migration-pin-consistent" in slugs


def test_a_migration_whose_repin_survives_a_later_default_model_edit_is_not_a_violation(tmp_path: Path) -> None:
    """The check is **membership**, not equality against ``[model_after]``:
    an operator may add a fallback entry to ``default_model`` after the re-pin without
    re-triggering the check."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        _seed_migration(
            conn,
            to_graph="gr_triage",
            model_after="claude-x",
            pin_graph="gr_triage",
            pin_default_model=["claude-x", "a-fallback-the-operator-added"],
        )
    assert HubInvariants(engine).run() == []


def test_a_migration_without_its_route_release_is_a_violation(tmp_path: Path) -> None:
    """``hub:migration-route-released`` — the graph/model re-pin landed but the route was
    never released: the other face of a torn ``migrate.`` write, leaving the re-pinned
    chunk holding its stale claim, unclaimable under the new graph (#90)."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        _seed_migration(
            conn,
            to_graph="gr_triage",
            model_after=None,
            pin_graph="gr_triage",
            pin_default_model=["m"],
            release_route=False,
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:migration-route-released" in slugs


def test_a_hub_landing_migration_retains_its_route_and_is_not_a_violation(tmp_path: Path) -> None:
    """``hub:migration-route-released`` exempts a migration landing on a hub-executed
    node: it deliberately retains the route, so no violation even with no
    ``route_released``."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        _seed_migration(
            conn,
            to_graph="gr_triage",
            model_after=None,
            pin_graph="gr_triage",
            pin_default_model=["m"],
            release_route=False,
            landed_executor="hub",
        )
    assert HubInvariants(engine).run() == []


def test_an_operator_restarts_repin_retains_its_route_and_is_not_a_violation(tmp_path: Path) -> None:
    """``hub:migration-route-released`` exempts the migration half of an eager cross-graph
    restart (#371): the holding runner keeps the route and re-enters on the target graph,
    so there is no release to owe."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        _seed_migration(
            conn,
            to_graph="gr_triage",
            model_after=None,
            pin_graph="gr_triage",
            pin_default_model=["m"],
            release_route=False,
            source="restart",
        )
    assert HubInvariants(engine).run() == []


def test_two_migrations_at_one_node_epoch_is_a_violation(tmp_path: Path) -> None:
    """``hub:one-migration-per-node-epoch`` — the idempotency natural key failed; a
    crash-replay double-landed the migration (#90)."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(
                stamp(hub.chunks, chunk_id="ch_1", graph_id="gr_triage", minted_at=_NOW, model="m")
            )
        )
        for mid in ("mg_1", "mg_2"):
            conn.execute(
                insert(hub.chunk_migrations).values(
                    stamp(
                        hub.chunk_migrations,
                        migration_id=mid,
                        chunk_id="ch_1",
                        from_node_id="nd_from",
                        from_graph_id="gr_src",
                        to_graph_id="gr_triage",
                        landed_node_id="nd_landed",
                        choice_name="migrate",
                        model_after=None,
                        epoch=1,
                        recorded_at=_NOW,
                    )
                )
            )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:one-migration-per-node-epoch" in slugs


def test_a_ref_closed_and_gone_both_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        for outcome in ("closed", "gone"):
            conn.execute(
                insert(hub.work_item_closures).values(
                    stamp(
                        hub.work_item_closures,
                        chunk_id="ch_1",
                        source="default",
                        ref="1",
                        outcome=outcome,
                        reason=None,
                        recorded_at=_NOW,
                    )
                )
            )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:no-double-terminal-closure" in slugs


def test_a_ref_closed_just_once_is_not_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.work_item_closures).values(
                stamp(
                    hub.work_item_closures,
                    chunk_id="ch_1",
                    source="default",
                    ref="1",
                    outcome="closed",
                    reason=None,
                    recorded_at=_NOW,
                )
            )
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:no-double-terminal-closure" not in slugs


def test_a_pending_intent_against_an_already_terminal_ref_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.work_item_closures).values(
                stamp(
                    hub.work_item_closures,
                    chunk_id="ch_1",
                    source="default",
                    ref="1",
                    outcome="closed",
                    reason=None,
                    recorded_at=_NOW,
                )
            )
        )
        conn.execute(
            insert(hub.close_intents).values(
                stamp(hub.close_intents, chunk_id="ch_1", source="default", ref="1", enqueued_at=_NOW, retired_at=None)
            )
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:no-pending-intent-against-terminal-ref" in slugs


def test_a_retired_intent_against_a_terminal_ref_is_not_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.work_item_closures).values(
                stamp(
                    hub.work_item_closures,
                    chunk_id="ch_1",
                    source="default",
                    ref="1",
                    outcome="closed",
                    reason=None,
                    recorded_at=_NOW,
                )
            )
        )
        conn.execute(
            insert(hub.close_intents).values(
                stamp(hub.close_intents, chunk_id="ch_1", source="default", ref="1", enqueued_at=_NOW, retired_at=_NOW)
            )
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:no-pending-intent-against-terminal-ref" not in slugs


def test_a_pending_intent_against_a_non_terminal_ref_is_not_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.close_intents).values(
                stamp(hub.close_intents, chunk_id="ch_1", source="default", ref="1", enqueued_at=_NOW, retired_at=None)
            )
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:no-pending-intent-against-terminal-ref" not in slugs


def test_a_landed_refs_pending_close_intent_is_not_a_violation(tmp_path: Path) -> None:
    """The legal history: ``_enqueue_close_intents`` fired for the landed chunk's still-open
    ref, so it carries a ``close_intents`` row."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(
                stamp(hub.chunks, chunk_id="ch_1", graph_id="gr_triage", minted_at=_NOW, model="m")
            )
        )
        conn.execute(
            insert(hub.chunk_work_refs).values(stamp(hub.chunk_work_refs, chunk_id="ch_1", source="default", ref="1"))
        )
        conn.execute(
            insert(hub.delivery_repo_landed).values(
                stamp(hub.delivery_repo_landed, chunk_id="ch_1", repo="widget", commit_hash="sha", landed_at=_NOW)
            )
        )
        conn.execute(
            insert(hub.close_intents).values(
                stamp(hub.close_intents, chunk_id="ch_1", source="default", ref="1", enqueued_at=_NOW, retired_at=None)
            )
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:no-unenqueued-closable-ref" not in slugs


def test_a_landed_refs_retired_close_intent_is_not_a_violation(tmp_path: Path) -> None:
    """A retired intent still proves the enqueue fired once; only a ref with no
    ``close_intents`` row at all is the violation."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(
                stamp(hub.chunks, chunk_id="ch_1", graph_id="gr_triage", minted_at=_NOW, model="m")
            )
        )
        conn.execute(
            insert(hub.chunk_work_refs).values(stamp(hub.chunk_work_refs, chunk_id="ch_1", source="default", ref="1"))
        )
        conn.execute(
            insert(hub.delivery_repo_landed).values(
                stamp(hub.delivery_repo_landed, chunk_id="ch_1", repo="widget", commit_hash="sha", landed_at=_NOW)
            )
        )
        conn.execute(
            insert(hub.close_intents).values(
                stamp(hub.close_intents, chunk_id="ch_1", source="default", ref="1", enqueued_at=_NOW, retired_at=_NOW)
            )
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:no-unenqueued-closable-ref" not in slugs


def test_a_landed_ref_already_terminal_with_no_intent_is_not_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(
                stamp(hub.chunks, chunk_id="ch_1", graph_id="gr_triage", minted_at=_NOW, model="m")
            )
        )
        conn.execute(
            insert(hub.chunk_work_refs).values(stamp(hub.chunk_work_refs, chunk_id="ch_1", source="default", ref="1"))
        )
        conn.execute(
            insert(hub.delivery_repo_landed).values(
                stamp(hub.delivery_repo_landed, chunk_id="ch_1", repo="widget", commit_hash="sha", landed_at=_NOW)
            )
        )
        conn.execute(
            insert(hub.work_item_closures).values(
                stamp(
                    hub.work_item_closures,
                    chunk_id="ch_1",
                    source="default",
                    ref="1",
                    outcome="closed",
                    reason=None,
                    recorded_at=_NOW,
                )
            )
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:no-unenqueued-closable-ref" not in slugs


def test_a_non_landed_refs_missing_intent_is_not_a_violation(tmp_path: Path) -> None:
    """A ref on a chunk that has neither landed nor been hand-completed owes no intent yet."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(
                stamp(hub.chunks, chunk_id="ch_1", graph_id="gr_triage", minted_at=_NOW, model="m")
            )
        )
        conn.execute(
            insert(hub.chunk_work_refs).values(stamp(hub.chunk_work_refs, chunk_id="ch_1", source="default", ref="1"))
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:no-unenqueued-closable-ref" not in slugs


def test_a_landed_ref_with_no_close_intent_and_no_terminal_outcome_is_a_violation(tmp_path: Path) -> None:
    """The exact bug this check exists to catch: a landing fact written directly to the
    store, bypassing ``chunk_rows.enqueue_close_intents`` — simulating a future landing-fact
    writer that forgot the call."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(
                stamp(hub.chunks, chunk_id="ch_1", graph_id="gr_triage", minted_at=_NOW, model="m")
            )
        )
        conn.execute(
            insert(hub.chunk_work_refs).values(stamp(hub.chunk_work_refs, chunk_id="ch_1", source="default", ref="1"))
        )
        conn.execute(
            insert(hub.delivery_repo_landed).values(
                stamp(hub.delivery_repo_landed, chunk_id="ch_1", repo="widget", commit_hash="sha", landed_at=_NOW)
            )
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:no-unenqueued-closable-ref" in slugs


def test_a_hand_completed_refs_missing_intent_is_a_violation(tmp_path: Path) -> None:
    """The completion path owes the same coverage as landing does."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(
                stamp(hub.chunks, chunk_id="ch_1", graph_id="gr_triage", minted_at=_NOW, model="m")
            )
        )
        conn.execute(
            insert(hub.chunk_work_refs).values(stamp(hub.chunk_work_refs, chunk_id="ch_1", source="default", ref="1"))
        )
        conn.execute(
            insert(hub.chunk_completed).values(
                stamp(hub.chunk_completed, chunk_id="ch_1", completed_at=_NOW, completed_by="test")
            )
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:no-unenqueued-closable-ref" in slugs


def test_a_grouped_chunks_missing_intent_is_not_a_violation(tmp_path: Path) -> None:
    """An ephemeral (grouped-away) chunk enqueues nothing by design; it owes
    no coverage either."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(
                stamp(hub.chunks, chunk_id="ch_1", graph_id="gr_triage", minted_at=_NOW, model="m")
            )
        )
        conn.execute(
            insert(hub.chunks).values(
                stamp(hub.chunks, chunk_id="ch_2", graph_id="gr_triage", minted_at=_NOW, model="m")
            )
        )
        conn.execute(
            insert(hub.chunk_work_refs).values(stamp(hub.chunk_work_refs, chunk_id="ch_1", source="default", ref="1"))
        )
        conn.execute(
            insert(hub.delivery_repo_landed).values(
                stamp(hub.delivery_repo_landed, chunk_id="ch_1", repo="widget", commit_hash="sha", landed_at=_NOW)
            )
        )
        conn.execute(
            insert(hub.chunk_grouped).values(
                stamp(hub.chunk_grouped, chunk_id="ch_1", grouped_into="ch_2", grouped_at=_NOW)
            )
        )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:no-unenqueued-closable-ref" not in slugs


def _segment_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "segment_id": "sg_1",
        "chunk_id": "ch_1",
        "node_id": "nd_build",
        "epoch": 1,
        "spawn_generation": 1,
        "runner_id": "r1",
        "turn_range_start": 0,
        "turn_range_end": 0,
        "final": True,
        "rejected": False,
        "byte_count": 10,
        "codec": "zlib",
        "content": b"",
        "content_digest": "dg_test",  # this suite inserts the raw row directly, bypassing the adapter that derives it
        "normalizer_version": "claude-code-jsonl/2",
        "received_at": _NOW,
    }
    values.update(overrides)
    return values


def test_a_segment_naming_no_chunk_is_a_violation(tmp_path: Path) -> None:
    """The production shape this check exists to name: sqlite enforces none of the schema's
    foreign keys, so a segment can outlive — or never have had — the chunk row it declares."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.transcript_segments).values(
                stamp(hub.transcript_segments, **_segment_values(chunk_id="ch_never_minted"))
            )
        )

    violations = [v for v in HubInvariants(engine).run() if v.invariant == "hub:segment-chunk-resolves"]

    assert [v.detail for v in violations] == ["segment sg_1 names unknown chunk ch_never_minted"]


def test_a_segment_naming_a_minted_chunk_is_not_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(
                stamp(hub.chunks, chunk_id="ch_1", graph_id="gr_triage", minted_at=_NOW, model="m")
            )
        )
        conn.execute(insert(hub.transcript_segments).values(stamp(hub.transcript_segments, **_segment_values())))

    slugs = {v.invariant for v in HubInvariants(engine).run()}

    assert "hub:segment-chunk-resolves" not in slugs


def _dependency_edge_values(**overrides: object) -> dict:
    values: dict = {
        "dependency_id": "dep_1",
        "dependent_chunk_id": "ch_dependent",
        "prerequisite_chunk_id": "ch_prereq",
        "declared_at": _NOW,
        "declared_by": "user:alice",
        "released_at": None,
        "released_by": None,
    }
    values.update(overrides)
    return values


def test_a_standing_dependency_cycle_is_a_violation(tmp_path: Path) -> None:
    """Hand-seeded counterexample: ``a`` depends on ``b``, ``b`` depends on ``a`` —
    a two-chunk cycle with no engine constraint to catch it."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunk_dependencies).values(
                stamp(
                    hub.chunk_dependencies,
                    **_dependency_edge_values(
                        dependency_id="dep_1", dependent_chunk_id="ch_a", prerequisite_chunk_id="ch_b"
                    ),
                )
            )
        )
        conn.execute(
            insert(hub.chunk_dependencies).values(
                stamp(
                    hub.chunk_dependencies,
                    **_dependency_edge_values(
                        dependency_id="dep_2", dependent_chunk_id="ch_b", prerequisite_chunk_id="ch_a"
                    ),
                )
            )
        )

    slugs = {v.invariant for v in HubInvariants(engine).run()}

    assert "hub:no-standing-dependency-cycle" in slugs


def test_an_acyclic_dependency_chain_is_not_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunk_dependencies).values(
                stamp(
                    hub.chunk_dependencies,
                    **_dependency_edge_values(
                        dependency_id="dep_1", dependent_chunk_id="ch_a", prerequisite_chunk_id="ch_b"
                    ),
                )
            )
        )
        conn.execute(
            insert(hub.chunk_dependencies).values(
                stamp(
                    hub.chunk_dependencies,
                    **_dependency_edge_values(
                        dependency_id="dep_2", dependent_chunk_id="ch_b", prerequisite_chunk_id="ch_c"
                    ),
                )
            )
        )

    slugs = {v.invariant for v in HubInvariants(engine).run()}

    assert "hub:no-standing-dependency-cycle" not in slugs


def test_a_released_edge_reopening_a_cycle_is_not_a_violation(tmp_path: Path) -> None:
    """A released row stays (never deleted); it must not itself close the cycle it once
    stood in."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunk_dependencies).values(
                stamp(
                    hub.chunk_dependencies,
                    **_dependency_edge_values(
                        dependency_id="dep_1", dependent_chunk_id="ch_a", prerequisite_chunk_id="ch_b"
                    ),
                )
            )
        )
        conn.execute(
            insert(hub.chunk_dependencies).values(
                stamp(
                    hub.chunk_dependencies,
                    **_dependency_edge_values(
                        dependency_id="dep_2",
                        dependent_chunk_id="ch_b",
                        prerequisite_chunk_id="ch_a",
                        released_at=_NOW,
                        released_by="user:alice",
                    ),
                )
            )
        )

    slugs = {v.invariant for v in HubInvariants(engine).run()}

    assert "hub:no-standing-dependency-cycle" not in slugs


def test_two_standing_rows_for_one_ordered_pair_is_a_violation(tmp_path: Path) -> None:
    """Hand-seeded counterexample: no database uniqueness constraint backs at-most-one
    standing edge per ordered pair — two standing rows for the same pair is the
    exact breach ``NoDuplicateStandingDependency`` exists to catch."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunk_dependencies).values(
                stamp(hub.chunk_dependencies, **_dependency_edge_values(dependency_id="dep_1"))
            )
        )
        conn.execute(
            insert(hub.chunk_dependencies).values(
                stamp(hub.chunk_dependencies, **_dependency_edge_values(dependency_id="dep_2"))
            )
        )

    slugs = {v.invariant for v in HubInvariants(engine).run()}

    assert "hub:no-duplicate-standing-dependency" in slugs


def test_a_declared_edge_after_a_release_of_the_same_pair_is_not_a_violation(tmp_path: Path) -> None:
    """Declaring after a release mints a fresh row, so this is legal: one released row plus
    one standing row for the same pair is not a duplicate-standing breach."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunk_dependencies).values(
                stamp(
                    hub.chunk_dependencies,
                    **_dependency_edge_values(dependency_id="dep_1", released_at=_NOW, released_by="user:alice"),
                )
            )
        )
        conn.execute(
            insert(hub.chunk_dependencies).values(
                stamp(hub.chunk_dependencies, **_dependency_edge_values(dependency_id="dep_2"))
            )
        )

    slugs = {v.invariant for v in HubInvariants(engine).run()}

    assert "hub:no-duplicate-standing-dependency" not in slugs


def test_a_standing_edge_onto_a_grouped_away_prerequisite_is_a_violation(tmp_path: Path) -> None:
    """Hand-seeded counterexample: the exact residual ``GroupService``'s missing claim
    lock leaves — a standing edge naming a prerequisite grouped away after the guard read."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(hub.chunk_dependencies).values(stamp(hub.chunk_dependencies, **_dependency_edge_values())))
        conn.execute(
            insert(hub.chunk_grouped).values(
                stamp(hub.chunk_grouped, chunk_id="ch_prereq", grouped_into="ch_survivor", grouped_at=_NOW)
            )
        )

    slugs = {v.invariant for v in HubInvariants(engine).run()}

    assert "hub:no-standing-dependency-onto-ephemeral-chunk" in slugs


def test_a_standing_edge_onto_a_deleted_prerequisite_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(hub.chunk_dependencies).values(stamp(hub.chunk_dependencies, **_dependency_edge_values())))
        conn.execute(
            insert(hub.chunk_deleted).values(
                stamp(hub.chunk_deleted, chunk_id="ch_prereq", deleted_at=_NOW, deleted_by="user:alice")
            )
        )

    slugs = {v.invariant for v in HubInvariants(engine).run()}

    assert "hub:no-standing-dependency-onto-ephemeral-chunk" in slugs


def test_a_standing_edges_dependent_gone_ephemeral_is_also_a_violation(tmp_path: Path) -> None:
    """Either endpoint qualifies — the dependent going ephemeral is as unwitnessed by the
    engine as the prerequisite doing so."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(hub.chunk_dependencies).values(stamp(hub.chunk_dependencies, **_dependency_edge_values())))
        conn.execute(
            insert(hub.chunk_deleted).values(
                stamp(hub.chunk_deleted, chunk_id="ch_dependent", deleted_at=_NOW, deleted_by="user:alice")
            )
        )

    slugs = {v.invariant for v in HubInvariants(engine).run()}

    assert "hub:no-standing-dependency-onto-ephemeral-chunk" in slugs


def test_a_released_edge_onto_an_ephemeral_chunk_is_not_a_violation(tmp_path: Path) -> None:
    """A released edge no longer stands, so it is exempt even onto an ephemeral chunk."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunk_dependencies).values(
                stamp(hub.chunk_dependencies, **_dependency_edge_values(released_at=_NOW, released_by="user:alice"))
            )
        )
        conn.execute(
            insert(hub.chunk_deleted).values(
                stamp(hub.chunk_deleted, chunk_id="ch_prereq", deleted_at=_NOW, deleted_by="user:alice")
            )
        )

    slugs = {v.invariant for v in HubInvariants(engine).run()}

    assert "hub:no-standing-dependency-onto-ephemeral-chunk" not in slugs


def test_a_backfilled_epoch_with_two_runners_lease_rows_is_clean(tmp_path: Path) -> None:
    """The real backfill owns an epoch by its first lease row, so a later level row from
    another runner — legacy data — is not a violation."""
    runner, engine = migrate_to(tmp_path, "20260930_1000_artifact_seq")
    at = "2026-09-29 12:00:00"
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=datetime(2026, 9, 29, tzinfo=UTC))
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=datetime(2026, 9, 29, tzinfo=UTC))
        for runner_id in ("r_a", "r_b"):
            conn.execute(
                sa.text(
                    "insert into lease_facts (chunk_id, epoch, runner_id, minted_at) "
                    "values ('ch_1', 1, :runner_id, :at)"
                ),
                {"runner_id": runner_id, "at": at},
            )
    runner.upgrade("head")
    try:
        assert [v for v in HubInvariants(engine).run() if v.invariant == "hub:epochs-owned"] == []
    finally:
        engine.dispose()


def _seed_landed_mid_flight(conn: sa.Connection, tenant: str) -> None:
    """A chunk in ``tenant`` whose ``delivery.landed`` fact sits beside a non-terminal newest transition."""
    conn.execute(
        insert(hub.chunks).values(
            stamp(hub.chunks, tenant_id=tenant, chunk_id="ch_1", graph_id="gr_1", minted_at=_NOW, model="m")
        )
    )
    conn.execute(
        insert(hub.lease_facts).values(
            stamp(hub.lease_facts, tenant_id=tenant, chunk_id="ch_1", epoch=2, runner_id="hub", minted_at=_NOW)
        )
    )
    conn.execute(
        insert(hub.transitions).values(
            stamp(
                hub.transitions,
                tenant_id=tenant,
                transition_id="tr_1",
                chunk_id="ch_1",
                graph_id="gr_1",
                from_node_id="nd_deliver",
                to_node_id="nd_verify",
                choice_name="landed",
                epoch=2,
                runner_id="hub",
                recorded_at=_NOW,
            )
        )
    )
    conn.execute(
        insert(hub.delivery_landed).values(
            stamp(hub.delivery_landed, tenant_id=tenant, chunk_id="ch_1", landed_at=_NOW)
        )
    )


def test_each_tenants_chunks_are_facts_checked_in_that_tenant(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    second = add_tenant(engine, "second")
    with engine.begin() as conn:
        _seed_landed_mid_flight(conn, second)

    breaches = [v for v in HubInvariants(engine).run() if v.invariant == "hub:merge-queue-single-state"]

    assert [v.detail for v in breaches] == [
        f"tenant {second}: chunk ch_1 is delivery.landed but newest transition targets nd_verify"
    ]


def test_a_closed_tenants_chunks_are_not_facts_checked(tmp_path: Path) -> None:
    """A closed tenant's rows are being drained, a chunk's facts before its record: what is left of a chunk is
    no longer whole, so it is not judged."""
    engine = _hub_engine(tmp_path)
    second = add_tenant(engine, "second")
    with engine.begin() as conn:
        _seed_landed_mid_flight(conn, second)
    close_tenant(engine, second)

    assert [v for v in HubInvariants(engine).run() if v.invariant == "hub:merge-queue-single-state"] == []


def _drained_tables(engine: sa.Engine, tenant: TenantId) -> set[str]:
    """The tables still holding a row of ``tenant``."""
    with engine.connect() as conn:
        return {
            table.name
            for table in hub.metadata.sorted_tables
            if is_tenant_owned(table)
            and conn.execute(
                hub_scoped(sa.select(sa.func.count()).select_from(table).where(table.c.tenant_id == tenant))
            ).scalar_one()
        }


def test_a_closed_tenant_mid_drain_breaks_no_check(tmp_path: Path) -> None:
    """A deletion drains a closed tenant's tables one after another, so partway through a restart outlives its epoch's
    owner and a runner landing's migration outlives the route release it carried. Neither is a breach: a closed
    tenant is checked only for what its deletion leaves behind."""
    engine = _hub_engine(tmp_path)
    second = add_tenant(engine, "second")
    with engine.begin() as conn:
        conn.execute(
            insert(hub.graphs).values(
                tenant_id=second, graph_id="gr_2", name="g", entry_node_id="nd_2", definition_yaml="", created_at=_NOW
            )
        )
        conn.execute(
            insert(hub.graph_nodes).values(
                tenant_id=second,
                node_id="nd_2",
                graph_id="gr_2",
                name="build",
                executor="runner",
                session="fresh",
                judged_by="worker",
            )
        )
        seed_drainable_history(conn, second, graph_id="gr_2", node_id="nd_2", at=_NOW, open_refs=3)
    assert HubInvariants(engine).run() == []
    close_tenant(engine, second)
    teardown = TenantTeardownStore(hub_level_connections(engine))

    half_drained: list[set[str]] = []
    while teardown.drain_batch(second, 1):
        assert HubInvariants(engine).run() == [], f"after draining down to {sorted(_drained_tables(engine, second))}"
        left = _drained_tables(engine, second)
        if "epoch_owners" not in left and {"chunk_restarts", "chunk_migrations", "chunk_completed"} <= left:
            half_drained.append(left)

    assert half_drained, "no batch boundary left a restart without its owner and a migration without its release"
    assert _drained_tables(engine, second) == set()


def test_a_membership_naming_a_tenant_absent_from_tenants_is_a_violation(tmp_path: Path) -> None:
    """A deletion removes the tenant's memberships with the tenant itself; one left behind is a leftover row too."""
    engine = _hub_engine(tmp_path)
    second = add_tenant(engine, "second")
    seed_membership(engine, second, "u_1", Role.CONTRIBUTOR)
    assert HubInvariants(engine).run() == []
    with engine.begin() as conn:
        conn.execute(hub_scoped(sa.delete(hub.tenants).where(hub.tenants.c.tenant_id == second)))

    breaches = [v for v in HubInvariants(engine).run() if v.invariant == "hub:no-row-of-a-deleted-tenant"]

    assert [v.detail for v in breaches] == [
        f"membership_facts holds 1 row(s) of tenant {second}, which is not in tenants",
        f"projects holds 1 row(s) of tenant {second}, which is not in tenants",
    ]


def test_a_row_naming_a_tenant_absent_from_tenants_is_a_violation(tmp_path: Path) -> None:
    """A deletion that removed its tenant's row but left some of the rows the tenant owns."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
    second = add_tenant(engine, "second")
    with engine.begin() as conn:
        conn.execute(
            insert(hub.graphs).values(
                stamp(
                    hub.graphs,
                    tenant_id=second,
                    graph_id="gr_2",
                    name="g",
                    entry_node_id="nd_1",
                    definition_yaml="",
                    created_at=_NOW,
                )
            )
        )
    assert HubInvariants(engine).run() == []
    with engine.begin() as conn:
        conn.execute(hub_scoped(sa.delete(hub.tenants).where(hub.tenants.c.tenant_id == second)))

    breaches = [v for v in HubInvariants(engine).run() if v.invariant == "hub:no-row-of-a-deleted-tenant"]

    assert [v.detail for v in breaches] == [
        f"graphs holds 1 row(s) of tenant {second}, which is not in tenants",
        f"projects holds 1 row(s) of tenant {second}, which is not in tenants",
    ]


_PROJECT_INVARIANTS = frozenset(
    {
        "hub:chunk-project-in-tenant",
        "hub:routine-scope-same-project",
        "hub:finding-scope-in-project",
        "hub:review-finding-project-is-chunks",
        "hub:proposal-cites-own-project",
        "hub:work-item-project-in-tenant",
        "hub:slug-not-current-and-former",
        "hub:chunk-sources-linked",
        "hub:landed-repository-linked",
    }
)


def _project_breaches(engine: sa.Engine, invariant: str) -> list[str]:
    return [v.detail for v in HubInvariants(engine).run() if v.invariant == invariant]


def _add_project(conn: sa.Connection, project_id: str, *, slug: str) -> None:
    """A second project in the store's only tenant, beside its ``default``."""
    conn.execute(
        insert(hub.projects).values(
            stamp(
                hub.projects,
                project_id=project_id,
                slug=slug,
                name=slug.title(),
                description="",
                revision=1,
                created_at=_NOW,
            )
        )
    )


def _scope_values(scope_id: str, project_id: str) -> dict[str, object]:
    return stamp(hub.scopes, scope_id=scope_id, slug=scope_id, description="", created_at=_NOW, project_id=project_id)


def _routine_values(routine_id: str, project_id: str, *, default_scope_id: str) -> dict[str, object]:
    return stamp(
        hub.routines,
        routine_id=routine_id,
        name=routine_id,
        graph_name="gr",
        default_scope_id=default_scope_id,
        created_at=_NOW,
        project_id=project_id,
    )


def _finding_values(finding_id: str, project_id: str, *, scope_id: str, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "finding_id": finding_id,
        "routine_name": "rtn_1",
        "scope_id": scope_id,
        "scope_slug": scope_id,
        "class_": "dead-code",
        "locus": "src/a.py",
        "summary": "s",
        "project_id": project_id,
    }
    values.update(overrides)
    return stamp(hub.findings, **values)


def _work_item_values(work_item_id: str, *, tenant: str, project_id: str) -> dict[str, object]:
    return stamp(
        hub.work_items,
        tenant_id=tenant,
        work_item_id=work_item_id,
        source="hub",
        ref=work_item_id,
        title="t",
        body="",
        author_kind="fleet",
        author_payload="{}",
        created_at=_NOW,
        edited_at=_NOW,
        project_id=project_id,
    )


def test_a_chunk_filed_under_another_tenants_project_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    first = default_tenant(engine)
    second = add_tenant(engine, "second")
    foreign = default_project(engine, second)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(
                stamp(
                    hub.chunks,
                    tenant_id=first,
                    chunk_id="ch_1",
                    graph_id="gr_1",
                    minted_at=_NOW,
                    model="m",
                    project_id=foreign,
                )
            )
        )

    assert _project_breaches(engine, "hub:chunk-project-in-tenant") == [
        f"chunk ch_1 of tenant {first} names project {foreign}, not a project of its tenant"
    ]


def test_a_chunk_of_its_tenants_project_is_not_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    second = add_tenant(engine, "second")
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(
                stamp(
                    hub.chunks,
                    tenant_id=second,
                    chunk_id="ch_1",
                    graph_id="gr_1",
                    minted_at=_NOW,
                    model="m",
                    project_id=default_project(engine, second),
                )
            )
        )

    assert {v.invariant for v in HubInvariants(engine).run()} & _PROJECT_INVARIANTS == set()


def test_a_closed_tenants_misfiled_chunk_is_not_judged(tmp_path: Path) -> None:
    """A closed tenant is checked only for what its deletion leaves behind, project rules included."""
    engine = _hub_engine(tmp_path)
    second = add_tenant(engine, "second")
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(
                stamp(
                    hub.chunks,
                    tenant_id=second,
                    chunk_id="ch_1",
                    graph_id="gr_1",
                    minted_at=_NOW,
                    model="m",
                    project_id="proj_never_minted",
                )
            )
        )
    assert _project_breaches(engine, "hub:chunk-project-in-tenant") != []
    close_tenant(engine, second)

    assert _project_breaches(engine, "hub:chunk-project-in-tenant") == []


def test_a_work_item_filed_under_another_tenants_project_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    first = default_tenant(engine)
    second = add_tenant(engine, "second")
    foreign = default_project(engine, second)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.work_items).values(_work_item_values("wi_1", tenant=first, project_id=default_project(engine)))
        )
        conn.execute(insert(hub.work_items).values(_work_item_values("wi_2", tenant=first, project_id=foreign)))

    assert _project_breaches(engine, "hub:work-item-project-in-tenant") == [
        f"work item wi_2 of tenant {first} names project {foreign}, not a project of its tenant"
    ]


def test_a_routine_over_another_projects_scope_is_a_violation(tmp_path: Path) -> None:
    """Both the default scope and a ``routine_scopes`` membership must be the routine's own project's."""
    engine = _hub_engine(tmp_path)
    home = default_project(engine)
    with engine.begin() as conn:
        _add_project(conn, "proj_other", slug="other")
        conn.execute(insert(hub.scopes).values(_scope_values("scp_home", home)))
        conn.execute(insert(hub.scopes).values(_scope_values("scp_other", "proj_other")))
        conn.execute(insert(hub.routines).values(_routine_values("rtn_ok", home, default_scope_id="scp_home")))
        conn.execute(insert(hub.routines).values(_routine_values("rtn_bad", home, default_scope_id="scp_other")))
        for routine_id, scope_id in (("rtn_ok", "scp_home"), ("rtn_ok", "scp_other")):
            conn.execute(
                insert(hub.routine_scopes).values(stamp(hub.routine_scopes, routine_id=routine_id, scope_id=scope_id))
            )

    assert _project_breaches(engine, "hub:routine-scope-same-project") == [
        f"routine rtn_bad of project {home} defaults to scope scp_other of project proj_other",
        f"routine rtn_ok of project {home} is scoped to scope scp_other of project proj_other",
    ]


def test_a_finding_or_finding_set_over_another_projects_scope_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    home = default_project(engine)
    with engine.begin() as conn:
        _add_project(conn, "proj_other", slug="other")
        conn.execute(insert(hub.scopes).values(_scope_values("scp_home", home)))
        conn.execute(insert(hub.scopes).values(_scope_values("scp_other", "proj_other")))
        conn.execute(insert(hub.findings).values(_finding_values("fin_ok", home, scope_id="scp_home")))
        conn.execute(insert(hub.findings).values(_finding_values("fin_bad", home, scope_id="scp_other")))
        conn.execute(
            insert(hub.finding_sets).values(
                stamp(
                    hub.finding_sets,
                    finding_set_id="fins_bad",
                    artifact_id="art_1",
                    chunk_id="ch_1",
                    scope_id="scp_home",
                    scope_slug="scp_home",
                    revisions="{}",
                    project_id="proj_other",
                )
            )
        )

    assert _project_breaches(engine, "hub:finding-scope-in-project") == [
        f"finding fin_bad of project {home} names scope scp_other of project proj_other",
        f"finding set fins_bad of project proj_other names scope scp_home of project {home}",
    ]


def test_a_review_finding_outside_its_chunks_project_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    home = default_project(engine)
    with engine.begin() as conn:
        _add_project(conn, "proj_other", slug="other")
        conn.execute(insert(hub.scopes).values(_scope_values("scp_home", home)))
        conn.execute(
            insert(hub.chunks).values(
                stamp(hub.chunks, chunk_id="ch_1", graph_id="gr_1", minted_at=_NOW, model="m", project_id="proj_other")
            )
        )
        for finding_id, raised_by in (("fin_review", "ch_1"), ("fin_unminted", "ch_never_minted")):
            conn.execute(
                insert(hub.findings).values(
                    _finding_values(
                        finding_id,
                        home,
                        scope_id="scp_home",
                        routine_name=None,
                        source="review",
                        severity="major",
                        raised_by_chunk_id=raised_by,
                    )
                )
            )

    assert _project_breaches(engine, "hub:review-finding-project-is-chunks") == [
        f"review finding fin_review of project {home} was raised by chunk ch_1 of project proj_other"
    ]


def test_a_proposal_citing_another_projects_finding_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    home = default_project(engine)
    with engine.begin() as conn:
        _add_project(conn, "proj_other", slug="other")
        conn.execute(insert(hub.scopes).values(_scope_values("scp_home", home)))
        conn.execute(insert(hub.scopes).values(_scope_values("scp_other", "proj_other")))
        conn.execute(insert(hub.findings).values(_finding_values("fin_home", home, scope_id="scp_home")))
        conn.execute(insert(hub.findings).values(_finding_values("fin_other", "proj_other", scope_id="scp_other")))
        conn.execute(
            insert(hub.garden_proposals).values(
                stamp(
                    hub.garden_proposals,
                    proposal_id="gprop_1",
                    routine_name="rtn_1",
                    class_="dead-code",
                    title="t",
                    body="",
                    created_at=_NOW,
                    project_id=home,
                )
            )
        )
        for finding_id in ("fin_home", "fin_other"):
            conn.execute(
                insert(hub.garden_proposal_findings).values(
                    stamp(hub.garden_proposal_findings, proposal_id="gprop_1", finding_id=finding_id)
                )
            )

    assert _project_breaches(engine, "hub:proposal-cites-own-project") == [
        f"proposal gprop_1 of project {home} cites finding fin_other of project proj_other"
    ]


def test_a_slug_both_current_and_former_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    tenant, home = default_tenant(engine), default_project(engine)
    with engine.begin() as conn:
        _add_project(conn, "proj_other", slug="other")
        for slug in ("other", "retired"):
            conn.execute(
                insert(hub.project_former_slugs).values(
                    stamp(hub.project_former_slugs, slug=slug, project_id=home, left_at=_NOW)
                )
            )

    assert _project_breaches(engine, "hub:slug-not-current-and-former") == [
        f"tenant {tenant}: slug other is project proj_other's and project {home}'s former slug"
    ]


def _hours(n: float) -> datetime:
    return _NOW + timedelta(hours=n)


def _source_fact(conn: sa.Connection, source: str, *, linked: bool, at: datetime, project_id: str = "proj_app") -> None:
    conn.execute(
        insert(hub.project_source_link_facts).values(
            stamp(
                hub.project_source_link_facts,
                project_id=project_id,
                source_name=source,
                linked=linked,
                set_at=at,
                set_by="op",
            )
        )
    )


def _repository_fact(
    conn: sa.Connection, repository: str, *, linked: bool, at: datetime, project_id: str = "proj_app"
) -> None:
    conn.execute(
        insert(hub.project_repository_link_facts).values(
            stamp(
                hub.project_repository_link_facts,
                project_id=project_id,
                repository_name=repository,
                linked=linked,
                set_at=at,
                set_by="op",
            )
        )
    )


def _mint(
    conn: sa.Connection, chunk_id: str, *, at: datetime, refs: tuple[str, ...], project_id: str = "proj_app"
) -> None:
    """A chunk of ``project_id`` minted ``at``, naming ``refs`` (``source:ref``) in order, the first originating."""
    conn.execute(
        insert(hub.chunks).values(
            stamp(hub.chunks, chunk_id=chunk_id, graph_id="gr_1", minted_at=at, model="m", project_id=project_id)
        )
    )
    for pointer in refs:
        source, ref = pointer.split(":")
        conn.execute(
            insert(hub.chunk_work_refs).values(stamp(hub.chunk_work_refs, chunk_id=chunk_id, source=source, ref=ref))
        )


def test_a_chunk_naming_a_source_its_project_did_not_link_is_a_violation(tmp_path: Path) -> None:
    """A chunk's first ref must have been linked by its mint; a ref folded in later, only linked at some time. The
    hub source needs no link, a later unlink breaks nothing, and a chunk minted before its project existed predates
    every link."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        _add_project(conn, "proj_app", slug="app")
        _source_fact(conn, "acme", linked=True, at=_hours(1))
        _source_fact(conn, "acme", linked=False, at=_hours(5))
        _source_fact(conn, "beta", linked=True, at=_hours(4))
        _mint(conn, "ch_early", at=_hours(0.5), refs=("acme:1",))
        _mint(conn, "ch_stray", at=_hours(2), refs=("hub:1", "gamma:7"))
        _mint(conn, "ch_folded", at=_hours(2), refs=("acme:2", "beta:3", "hub:2"))
        _mint(conn, "ch_late_origin", at=_hours(3), refs=("beta:4",))
        _mint(conn, "ch_carried", at=_hours(-1), refs=("gamma:8",))

    assert _project_breaches(engine, "hub:chunk-sources-linked") == [
        "chunk ch_early of project proj_app was minted from acme:1, a source its project had not linked by then",
        "chunk ch_late_origin of project proj_app was minted from beta:4, a source its project had not linked by then",
        "chunk ch_stray of project proj_app names gamma:7, a source its project never linked",
    ]


def test_a_landing_in_a_repository_its_project_had_not_linked_is_a_violation(tmp_path: Path) -> None:
    """A landing must name a repository its chunk's project linked by the time it landed; an unlink while the step
    ran does not undo that, and a landing naming no repository predates the name and is not judged."""
    engine = _hub_engine(tmp_path)
    home = default_project(engine)
    with engine.begin() as conn:
        _add_project(conn, "proj_app", slug="app")
        _repository_fact(conn, "app-repo", linked=True, at=_hours(1))
        _repository_fact(conn, "app-repo", linked=False, at=_hours(2))
        _mint(conn, "ch_app", at=_hours(1), refs=())
        _mint(conn, "ch_home", at=_hours(1), refs=(), project_id=home)
        for chunk_id, repo, name, at in (
            ("ch_app", "acme/app", "app-repo", _hours(3)),
            ("ch_app", "acme/early", "app-repo", _hours(0.5)),
            ("ch_app", "acme/other", "other-repo", _hours(3)),
            ("ch_app", "acme/carried", None, _hours(3)),
            ("ch_home", "acme/app", "app-repo", _hours(1.5)),
        ):
            conn.execute(
                insert(hub.delivery_repo_landed).values(
                    stamp(
                        hub.delivery_repo_landed,
                        chunk_id=chunk_id,
                        repo=repo,
                        commit_hash="c0ffee",
                        landed_at=at,
                        repository_name=name,
                    )
                )
            )

    assert _project_breaches(engine, "hub:landed-repository-linked") == [
        "chunk ch_app of project proj_app landed acme/early in repository app-repo, which its project had not "
        "linked by then",
        "chunk ch_app of project proj_app landed acme/other in repository other-repo, which its project had not "
        "linked by then",
        f"chunk ch_home of project {home} landed acme/app in repository app-repo, which its project had not "
        "linked by then",
    ]


def test_a_carried_over_store_keeps_every_project_invariant(tmp_path: Path) -> None:
    """Rows written before projects existed, in two tenants, each land in their own tenant's ``default`` project. A
    carried chunk was minted before the migration created that project, and a carried landing names no repository."""
    runner, engine = migrate_to(tmp_path, "20261008_1720_tenant_deletion_closed_at")
    carried_mint = MIGRATION_CLOCK.now() - timedelta(days=1)
    seeding = hub_scoped_engine(engine)
    with seeding.begin() as conn:
        first = conn.execute(sa.select(hub.tenants.c.tenant_id)).scalar_one()
        conn.execute(
            insert(hub.tenants).values(tenant_id="tnt_second", name="second", created_at=_NOW, created_by="test")
        )
        for n, tenant in enumerate((first, "tnt_second")):
            scope, routine, chunk, finding = f"scp_{n}", f"rtn_{n}", f"ch_{n}", f"fin_{n}"
            conn.execute(
                insert(hub.scopes).values(tenant_id=tenant, scope_id=scope, slug="s", description="", created_at=_NOW)
            )
            conn.execute(
                insert(hub.routines).values(
                    tenant_id=tenant,
                    routine_id=routine,
                    name="r",
                    graph_name="g",
                    default_scope_id=scope,
                    created_at=_NOW,
                )
            )
            conn.execute(insert(hub.routine_scopes).values(tenant_id=tenant, routine_id=routine, scope_id=scope))
            conn.execute(
                insert(hub.chunks).values(
                    tenant_id=tenant, chunk_id=chunk, graph_id="gr_1", minted_at=carried_mint, model="m"
                )
            )
            conn.execute(
                insert(hub.work_sources).values(
                    tenant_id=tenant,
                    name="acme",
                    provider="github",
                    locator="acme/app",
                    annotate=False,
                    revision=1,
                    created_at=_NOW,
                    created_by="test",
                )
            )
            # A stored source, a source never stored as a record, and the built-in hub source.
            for source in ("acme", "default", "hub"):
                conn.execute(
                    insert(hub.chunk_work_refs).values(tenant_id=tenant, chunk_id=chunk, source=source, ref="1")
                )
            conn.execute(
                insert(hub.delivery_repo_landed).values(
                    tenant_id=tenant, chunk_id=chunk, repo="acme/app", commit_hash="c0ffee", landed_at=_NOW
                )
            )
            conn.execute(
                insert(hub.findings).values(
                    tenant_id=tenant,
                    finding_id=finding,
                    scope_id=scope,
                    scope_slug="s",
                    class_="c",
                    locus="a.py",
                    summary="s",
                    source="review",
                    raised_by_chunk_id=chunk,
                )
            )
            conn.execute(
                insert(hub.garden_proposals).values(
                    tenant_id=tenant,
                    proposal_id=f"gprop_{n}",
                    routine_name="r",
                    class_="c",
                    title="t",
                    body="",
                    created_at=_NOW,
                )
            )
            conn.execute(
                insert(hub.garden_proposal_findings).values(
                    tenant_id=tenant, proposal_id=f"gprop_{n}", finding_id=finding
                )
            )
            conn.execute(
                insert(hub.work_items).values(
                    tenant_id=tenant,
                    work_item_id=f"wi_{n}",
                    source="hub",
                    ref="1",
                    title="t",
                    body="",
                    author_kind="fleet",
                    author_payload="{}",
                    created_at=_NOW,
                    edited_at=_NOW,
                )
            )
    runner.upgrade("head")
    try:
        assert {v.invariant for v in HubInvariants(engine).run()} & _PROJECT_INVARIANTS == set()
    finally:
        engine.dispose()

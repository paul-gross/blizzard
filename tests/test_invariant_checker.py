"""Component coverage for the facts-level invariant checker (``bzh:invariant-checker``).

Migrates real hub + runner stores, asserts a clean store yields no violations, then
injects each kind of corruption and asserts the matching invariant is named. Every
corruption is injected on a head-migrated store, the only shape the checker is
contracted to read."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import insert

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.invariants import HubInvariants, RunnerInvariants
from blizzard.hub.runtime import init_environment as init_hub
from blizzard.hub.store import schema as hub
from blizzard.runner.runtime import init_environment as init_runner
from blizzard.runner.store import schema as runner

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 14, tzinfo=UTC)
_PR_OPENED_UNIQUE = "uq_delivery_pr_opened_chunk_repo"  # added by 20260716_2206_hub_pr_opened_idempotent


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
            conn.execute(
                insert(runner.leases).values(
                    lease_id=lease_id, chunk_id="ch_1", epoch=1, runner_id="r", created_at=_NOW
                )
            )
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


def test_gapped_transcript_outbound_seq_is_a_violation(tmp_path: Path) -> None:
    """The transcript lane's own gapless-seq check — never `outbound_buffer`'s (D3)."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for seq in (1, 2, 4):  # 3 is missing — a hole in the FIFO buffer
            conn.execute(
                insert(runner.transcript_outbound_buffer).values(
                    seq=seq, kind="transcript.delta", segment_id="seg_1", chunk_id="ch_1", payload="{}", created_at=_NOW
                )
            )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:gapless-transcript-outbound-seq" in slugs
    # A fact-lane gap does not trip the transcript lane's own check, and vice versa.
    assert "runner:gapless-outbound-seq" not in slugs


def _segment_row(**overrides: object) -> dict:
    row: dict = {
        "segment_id": "seg_1",
        "chunk_id": "ch_1",
        "node_id": "nd_build",
        "epoch": 1,
        "generation": 1,
        "lease_id": "lease_1",
        "session_id": "sess_1",
        "cursor": None,
        "shipped_bytes": 0,
        "shipped_turns": 0,
        "truncated_reason": None,
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
                    kind="transcript.final",
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
                seq=1, kind="transcript.final", segment_id="seg_1", chunk_id="ch_1", payload="{}", created_at=_NOW
            )
        )
    slugs = {v.invariant for v in RunnerInvariants(engine).run()}
    assert "runner:transcript-segment-finalized-exactly-once" not in slugs


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


def test_duplicate_nudge_fact_for_one_lease_epoch_is_a_violation(tmp_path: Path) -> None:
    """`record_nudge_fired` is an insert never an upsert, gated in code not by a DB
    constraint (issue #113); two rows for the same ``(lease, epoch)`` mean that guard
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
                    chunk_id="ch_1", repo="toy-api", commit_hash="abc", landed_at=_NOW
                )
            )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:per-repo-land-idempotent" in slugs


def test_duplicate_pr_opened_is_a_violation(tmp_path: Path) -> None:
    """See the module docstring — the constraint is dropped so the check behind it is
    observable."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        batch_op = Operations(MigrationContext.configure(conn))
        with batch_op.batch_alter_table("delivery_pr_opened") as batch:
            batch.drop_constraint(_PR_OPENED_UNIQUE, type_="unique")

    with engine.begin() as conn:
        for pk in (1, 2):
            conn.execute(
                insert(hub.delivery_pr_opened).values(
                    id=pk,
                    chunk_id="ch_1",
                    repo="acme/widget",
                    pr_number=1,
                    pr_url="http://forge/acme/widget/pull/1",
                    commit_hash="abc123",
                    opened_at=_NOW,
                )
            )
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:pr-opened-idempotent" in slugs


def test_duplicate_route_seq_across_tables_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            insert(hub.route_created).values(
                route_id="rt_1", chunk_id="ch_1", runner_id="r", workspace_id="w", created_at=_NOW, seq=1
            )
        )
        # Same chunk, same seq as the create above — the exact race #41's tiebreak
        # closed: two route writes both computed seq=1 for chunk ch_1.
        conn.execute(insert(hub.route_released).values(chunk_id="ch_1", released_at=_NOW, seq=1))
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:route-seq-unique" in slugs


def test_transition_epoch_beyond_latest_lease_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(hub.lease_facts).values(chunk_id="ch_1", epoch=1, runner_id="r", minted_at=_NOW))
        conn.execute(
            insert(hub.transitions).values(
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
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:epoch-consistent-transitions" in slugs


def test_landed_fact_without_terminal_transition_is_a_two_state_violation(tmp_path: Path) -> None:
    """``hub:merge-queue-single-state`` — a whole-chunk ``delivery.landed`` fact paired
    with a non-terminal newest transition reads as both landed and mid-flight (issue #63);
    defense-in-depth, since a real store never writes this shape."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(hub.chunks).values(chunk_id="ch_1", graph_id="gr_1", minted_at=_NOW, model="m"))
        conn.execute(insert(hub.lease_facts).values(chunk_id="ch_1", epoch=2, runner_id="hub", minted_at=_NOW))
        # Newest transition targets a post-merge worker node, NOT the reserved terminal.
        conn.execute(
            insert(hub.transitions).values(
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
        conn.execute(insert(hub.delivery_landed).values(chunk_id="ch_1", landed_at=_NOW))
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:merge-queue-single-state" in slugs


def test_merged_into_post_merge_node_is_not_a_violation(tmp_path: Path) -> None:
    """#63's legal shape: a chunk merged into a post-merge node is clean, never flagged —
    it carries per-repo ``delivery.repo_landed`` facts but no whole-chunk
    ``delivery.landed`` fact, so it derives its live status rather than DONE."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(hub.chunks).values(chunk_id="ch_1", graph_id="gr_1", minted_at=_NOW, model="m"))
        conn.execute(insert(hub.lease_facts).values(chunk_id="ch_1", epoch=2, runner_id="hub", minted_at=_NOW))
        conn.execute(
            insert(hub.transitions).values(
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
        conn.execute(
            insert(hub.delivery_repo_landed).values(chunk_id="ch_1", repo="toy-api", commit_hash="abc", landed_at=_NOW)
        )
    assert HubInvariants(engine).run() == []


def test_two_open_pause_parks_on_one_lease_is_a_violation(tmp_path: Path) -> None:
    """``runner:one-open-pause-park-per-lease`` — PULL's park guard is the only thing
    keeping a standing pause to a single open park (issue #46, plan §7)."""
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
) -> None:
    """A chunk pinned to (pin_graph, pin_default_model) with one migration targeting
    to_graph (#90). ``release_route=False`` seeds a torn write where the migration fact
    landed but the route release did not. ``landed_executor`` seeds a ``graph_nodes``
    row for the landing node; a ``"hub"`` landing (issue #111) is exempt from the
    route-released check — left None, the assertion applies."""
    # `chunks.model` is retained-and-unread since #144; the re-pin the invariant checks
    # lands in `default_model`, so that is what a seeded pin has to carry.
    conn.execute(
        insert(hub.chunks).values(
            chunk_id="ch_1",
            graph_id=pin_graph,
            minted_at=_NOW,
            model="unread-since-144",
            default_model=json.dumps(pin_default_model) if pin_default_model else None,
        )
    )
    if landed_executor is not None:
        conn.execute(
            insert(hub.graph_nodes).values(
                node_id="nd_landed",
                graph_id=to_graph,
                name="build",
                executor=landed_executor,
                session="fresh",
                judged_by="worker",
            )
        )
    conn.execute(
        insert(hub.chunk_migrations).values(
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
        )
    )
    if release_route:
        conn.execute(insert(hub.route_released).values(chunk_id="ch_1", released_at=_NOW, seq=1))


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
    """Issue #144 — the check is **membership**, not equality against ``[model_after]``:
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
    node (issue #111): it deliberately retains the route, so no violation even with no
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


def test_two_migrations_at_one_node_epoch_is_a_violation(tmp_path: Path) -> None:
    """``hub:one-migration-per-node-epoch`` — the idempotency natural key failed; a
    crash-replay double-landed the migration (#90)."""
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(hub.chunks).values(chunk_id="ch_1", graph_id="gr_triage", minted_at=_NOW, model="m"))
        for mid in ("mg_1", "mg_2"):
            conn.execute(
                insert(hub.chunk_migrations).values(
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
    slugs = {v.invariant for v in HubInvariants(engine).run()}
    assert "hub:one-migration-per-node-epoch" in slugs

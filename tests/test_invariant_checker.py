"""Component coverage for the facts-level invariant checker (``bzh:invariant-checker``).

Migrates real hub + runner stores, asserts a clean store yields no violations, then
injects each kind of corruption and asserts the matching invariant is named. This is the
library the kill-9 sweep asserts after every armed crash — here it is exercised without
subprocesses so the default gate covers it.

Every corruption is injected on a **head-migrated** store, because that is the only
shape the checker is contracted to read: it runs against real stores, and a daemon
refuses to start on a revision mismatch (``bzh:manual-migrations``), so a store it
ever sees is at head.

``hub:pr-opened-idempotent`` needs one extra step to honor that. The violation it
guards is one ``uq_delivery_pr_opened_chunk_repo`` (20260716_2206_hub_pr_opened_idempotent)
makes impossible to *write* at head — a raw two-insert seed the way every sibling
check does it dies on the constraint instead of producing a violation. So that test
drops the constraint on an otherwise head-shaped store (via the same
``batch_alter_table`` the pr-opened-idempotent revision adds it with) and then seeds the duplicate: the check is
defense in depth *behind* the constraint, and this is what proves it still fires if
the constraint is ever absent. Seeding an older revision instead would leave the
store missing columns that other checks in the same pass read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import insert

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.invariants import check_hub_store, check_runner_store
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
    assert check_runner_store(_runner_engine(tmp_path)) == []
    assert check_hub_store(_hub_engine(tmp_path)) == []


def test_two_live_leases_for_one_chunk_is_a_violation(tmp_path: Path) -> None:
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for lease_id in ("lease_a", "lease_b"):
            conn.execute(
                insert(runner.leases).values(
                    lease_id=lease_id, chunk_id="ch_1", epoch=1, runner_id="r", created_at=_NOW
                )
            )
    slugs = {v.invariant for v in check_runner_store(engine)}
    assert "runner:one-live-lease-per-chunk" in slugs
    # Closing one lease clears the violation (facts-not-status: a closure is the fact).
    with engine.begin() as conn:
        conn.execute(
            insert(runner.lease_closures).values(
                lease_id="lease_b", chunk_id="ch_1", node_id="nd", reason="transitioned", closed_at=_NOW
            )
        )
    assert check_runner_store(engine) == []


def test_env_bound_to_two_chunks_is_a_violation(tmp_path: Path) -> None:
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for chunk_id in ("ch_1", "ch_2"):
            conn.execute(
                insert(runner.env_bindings).values(
                    chunk_id=chunk_id, environment_id="env_shared", workdir="/w", bound_at=_NOW
                )
            )
    slugs = {v.invariant for v in check_runner_store(engine)}
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
    slugs = {v.invariant for v in check_runner_store(engine)}
    assert "runner:gapless-outbound-seq" in slugs


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
    slugs = {v.invariant for v in check_runner_store(engine)}
    assert "runner:usage-attributed-once" in slugs


def test_distinct_generation_or_kind_usage_rows_are_not_a_violation(tmp_path: Path) -> None:
    """A retry/resume within a lease mints a new generation, and a judgement a different
    kind — each a genuinely new row, not a duplicate. The checker stays green over them."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.usage_facts).values(**_usage_row(generation=1, kind="spawn")))
        conn.execute(insert(runner.usage_facts).values(**_usage_row(generation=2, kind="resume")))
        conn.execute(insert(runner.usage_facts).values(**_usage_row(generation=1, kind="judge")))
    assert check_runner_store(engine) == []


def test_duplicate_repo_land_is_a_violation(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    with engine.begin() as conn:
        for _ in range(2):
            conn.execute(
                insert(hub.delivery_repo_landed).values(
                    chunk_id="ch_1", repo="toy-api", commit_hash="abc", landed_at=_NOW
                )
            )
    slugs = {v.invariant for v in check_hub_store(engine)}
    assert "hub:per-repo-land-idempotent" in slugs


def test_duplicate_pr_opened_is_a_violation(tmp_path: Path) -> None:
    """Head-migrated, then the constraint dropped (see the module docstring):
    ``uq_delivery_pr_opened_chunk_repo`` makes this duplicate unwritable at head, so the
    check behind it is only observable with the constraint gone. The store stays
    head-shaped, so every other check in the same pass still reads its own columns."""
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
    slugs = {v.invariant for v in check_hub_store(engine)}
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
    slugs = {v.invariant for v in check_hub_store(engine)}
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
    slugs = {v.invariant for v in check_hub_store(engine)}
    assert "hub:epoch-consistent-transitions" in slugs


def test_landed_fact_without_terminal_transition_is_a_two_state_violation(tmp_path: Path) -> None:
    """``hub:merge-queue-single-state`` — #63's "a merged chunk is never left non-terminal".

    A whole-chunk ``delivery.landed`` fact paired with a NON-terminal newest transition is a
    chunk that is merged yet not-terminal — read as both landed and mid-flight (two states at
    once), the un-merged corruption. The checker fires: this is the facts-level embodiment of
    "DONE derives from *reaching* the terminal transition, not from the landed fact." A real
    store never writes this shape (``finalize_delivery`` writes the landed fact and the terminal
    transition atomically), so the check is defense-in-depth behind that atomic write."""
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
    slugs = {v.invariant for v in check_hub_store(engine)}
    assert "hub:merge-queue-single-state" in slugs


def test_merged_into_post_merge_node_is_not_a_violation(tmp_path: Path) -> None:
    """#63's legal shape: a chunk merged into a post-merge node is clean, never flagged.

    Per-repo ``delivery.repo_landed`` facts (the merge happened), a NON-terminal newest
    transition into the post-merge node, and NO whole-chunk ``delivery.landed`` fact. The
    chunk is merged-but-running: it carries no whole-chunk terminal fact, so it derives its
    live status rather than DONE, and the checker must not read it as un-merged/two-state.
    This is the exact shape #63's coordinator ``_landed`` non-terminal branch produces."""
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
    assert check_hub_store(engine) == []


def test_two_open_pause_parks_on_one_lease_is_a_violation(tmp_path: Path) -> None:
    """``runner:one-open-pause-park-per-lease`` — PULL's park guard is the only thing
    keeping a standing pause to a single open park (issue #46, plan §7)."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        for _ in range(2):  # the same standing pause parked twice — the dropped-guard shape
            conn.execute(insert(runner.pause_parks).values(lease_id="lease_a", chunk_id="ch_1", parked_at=_NOW))
    slugs = {v.invariant for v in check_runner_store(engine)}
    assert "runner:one-open-pause-park-per-lease" in slugs

    # Resuming the lease closes both parks (the resume is at/after each) — no violation.
    with engine.begin() as conn:
        conn.execute(insert(runner.pause_park_resumes).values(lease_id="lease_a", resumed_at=_NOW))
    assert check_runner_store(engine) == []


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
    assert check_runner_store(engine) == []


def test_open_pause_parks_on_different_leases_are_not_a_violation(tmp_path: Path) -> None:
    """The invariant is per-lease: two chunks paused at once is the normal world."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(insert(runner.pause_parks).values(lease_id="lease_a", chunk_id="ch_1", parked_at=_NOW))
        conn.execute(insert(runner.pause_parks).values(lease_id="lease_b", chunk_id="ch_2", parked_at=_NOW))
    assert check_runner_store(engine) == []


def test_a_double_park_after_a_repause_is_still_a_violation(tmp_path: Path) -> None:
    """The ``>=`` correlation in the checker's mirror is load-bearing.

    A checker that treated *any* resume on the lease as closing *every* park would read
    this lease as unparked and miss the breach — so a dropped PULL guard would go
    undetected on any lease that had ever been resumed, which is every long-lived one.
    The checker must agree with the store's ``_pause_park_is_open``: only a resume at or
    after a given park closes *that* park.
    """
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
    slugs = {v.invariant for v in check_runner_store(engine)}
    assert "runner:one-open-pause-park-per-lease" in slugs

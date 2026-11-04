"""Cross-graph migration — the store write + derivations.

A :class:`MigrationFact` makes ``current_node_id`` the landing node and status ``ready``;
``record_migration`` re-pins the graph, releases the route, and persists artifacts in one
idempotent write. adds ``IntendedMigration`` and the ``clear_intent`` flag.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunk.model import (
    ChunkFacts,
    MigrationFact,
    TransitionFact,
)
from blizzard.hub.domain.graph.authoring import Reification
from blizzard.hub.domain.graph.model import GraphDoc

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Unit — derivations


def _migrated_facts(
    *, landed: str | None, model: str | None = None, landed_executor: Executor = Executor.RUNNER
) -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        promoted=True,
        transitions=[
            TransitionFact(
                to_node_id="nd_from",
                to_node_executor=Executor.RUNNER,
                epoch=1,
                recorded_at=_T0,
                from_node_id=None,
                graph_id="gr_a",
            )
        ],
        migrations=[
            MigrationFact(
                from_node_id="nd_from",
                from_graph_id="gr_a",
                to_graph_id="gr_b",
                landed_node_id=landed,
                choice_name="migrate",
                model=model,
                epoch=1,
                recorded_at=_T0 + timedelta(minutes=1),
                landed_node_executor=landed_executor,
            )
        ],
    )


def test_after_a_migration_the_current_node_is_the_landing_node_and_status_is_ready() -> None:
    facts = _migrated_facts(landed="nd_landed")
    assert facts.current_node_id() == "nd_landed"
    # A runner-executed landing node re-queues the chunk claimable (regression).
    assert facts.status() is ChunkStatus.READY
    # The fact carries the re-pinned model.
    migration = facts.newest_migration()
    assert migration is not None and migration.model is None


def test_a_migration_landing_on_a_hub_node_derives_delivering() -> None:
    # A migration re-pinning the chunk onto a hub-executed node is retained
    # by the hub, exactly as a transition into one is — never wrongly derived READY.
    facts = _migrated_facts(landed="nd_landed", landed_executor=Executor.HUB)
    assert facts.status() is ChunkStatus.DELIVERING


def test_a_migration_with_a_repinned_model_carries_it() -> None:
    facts = _migrated_facts(landed="nd_landed", model="claude-sonnet-5")
    migration = facts.newest_migration()
    assert migration is not None and migration.model == "claude-sonnet-5"


def test_a_null_landing_node_falls_through_to_none_the_schema_entry_allowance() -> None:
    # A NULL landed_node_id reads as "the target's entry": current_node_id returns None,
    # which the call sites resolve via `... or graph.entry_node_id`.
    facts = _migrated_facts(landed=None)
    assert facts.current_node_id() is None
    assert facts.status() is ChunkStatus.READY


def test_landing_node_is_name_match_else_entry() -> None:
    graph = Reification.of(
        GraphDoc.of(
            {
                "name": "triage",
                "entry": "intake",
                "nodes": {
                    "intake": {
                        "executor": "runner",
                        "judgement": {"prompt": "p", "choices": {"go": {"description": "d", "to": "build"}}},
                    },
                    "build": {
                        "executor": "runner",
                        "judgement": {"prompt": "p", "choices": {"ok": {"description": "d", "to": "done"}}},
                    },
                },
            }
        ),
        FixedClock(_T0),
    ).graph
    build = graph.node_by_name("build")
    assert build is not None
    assert MigrationFact.landing_node(graph, "build") == build.node_id  # name match
    assert MigrationFact.landing_node(graph, "no-such-node") == graph.entry_node_id  # entry fallback
    assert MigrationFact.landing_node(graph, None) == graph.entry_node_id

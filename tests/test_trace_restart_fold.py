"""A cross-graph restart folds to one arrival, (unit tier)."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.chunk.model import MigrationSource
from blizzard.hub.domain.observability.tracing.facts import TracedMigration, TracedRestart
from blizzard.hub.domain.observability.tracing.position import movement_arrivals, position_at
from tests import trace_fixtures as fx

pytestmark = pytest.mark.unit


def _cross_graph_restart(from_node: str | None = "g1-review"):
    return fx.make_facts(
        transitions=(fx.to("g1", "review", 10, 1),),
        migrations=(TracedMigration(2, fx.at(20), "g1", "g2", from_node_id=from_node, source=MigrationSource.RESTART),),
        restarts=(TracedRestart(2, fx.at(20), "g2", "g2-review", from_graph_id="g1", from_node_id=from_node),),
    )


def test_a_cross_graph_restart_is_one_arrival() -> None:
    arrivals = movement_arrivals(_cross_graph_restart())
    assert [(a.recorded_at, a.epoch, a.graph_id, a.node_id) for a in arrivals] == [
        (fx.at(10), 1, "g1", "g1-review"),
        (fx.at(20), 2, "g2", "g2-review"),
    ]


def test_a_cross_graph_restart_counts_one_visit_at_the_destination() -> None:
    facts = _cross_graph_restart()
    position = position_at(facts, fx.at(25))
    assert (position.graph_id, position.node_name, position.visit) == ("g2", "review", 2)


def test_a_restart_as_the_first_movement_starts_in_its_source_graph() -> None:
    facts = fx.make_facts(
        migrations=(
            TracedMigration(1, fx.at(10), "g1", "g2", from_node_id="g1-build", source=MigrationSource.RESTART),
        ),
        restarts=(TracedRestart(1, fx.at(10), "g2", "g2-review", from_graph_id="g1", from_node_id="g1-build"),),
    )
    assert position_at(facts, fx.at(5)).graph_id == "g1"
    after = position_at(facts, fx.at(15))
    assert (after.graph_id, after.node_name, after.visit) == ("g2", "review", 1)


def test_a_non_restart_migration_still_arrives_alongside_a_restart_elsewhere() -> None:
    facts = fx.make_facts(
        migrations=(TracedMigration(1, fx.at(10), "g1", "g2", from_node_id="g1-build", source=MigrationSource.INTENT),),
        restarts=(TracedRestart(2, fx.at(20), "g2", "g2-build"),),
    )
    assert [(a.epoch, a.node_id) for a in movement_arrivals(facts)] == [(1, "g2-build"), (2, "g2-build")]

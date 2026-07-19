"""Cross-graph migration — the store write + derivations (issue #90, Phase 3).

Unit tier: a :class:`MigrationFact` makes ``current_node_id`` the landing node,
``derive_chunk_status`` ``ready`` (re-queued, not ``done``/``delivering`` off the
superseded pre-migration transition), and the pure ``landing_node`` resolver picks
name-match-else-entry. Component tier: ``record_migration`` re-pins the graph (+ model),
releases the route, and persists the submitting node-step's artifacts in one write
(MUST-FIX 1); a replay is idempotent (no duplicate artifacts, no transition row).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import select

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.graph import Executor, parse_graph_doc
from blizzard.hub.domain.graph_authoring import reify_graph
from blizzard.hub.domain.work import (
    ChunkFacts,
    ChunkStatus,
    IWriteChunkRepository,
    MigrationFact,
    TransitionFact,
    current_node_id,
    derive_chunk_status,
    landing_node,
    newest_migration,
)
from blizzard.hub.store import schema as s
from tests.support import build_hub, pointer_token, report_lease

unit = pytest.mark.unit
component = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_POINTER = {"source": "default", "ref": "9"}

_SRC_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: done
          to: done
        fail:
          description: retry
          to: build
"""


# --------------------------------------------------------------------------- #
# Unit — derivations
# --------------------------------------------------------------------------- #


def _migrated_facts(*, landed: str | None, model: str | None = None) -> ChunkFacts:
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
            )
        ],
    )


@unit
def test_after_a_migration_the_current_node_is_the_landing_node_and_status_is_ready() -> None:
    facts = _migrated_facts(landed="nd_landed")
    assert current_node_id(facts) == "nd_landed"
    assert derive_chunk_status(facts) is ChunkStatus.READY
    # The fact carries the re-pinned model for the audit/history surface.
    migration = newest_migration(facts)
    assert migration is not None and migration.model is None


@unit
def test_a_migration_with_a_repinned_model_carries_it() -> None:
    facts = _migrated_facts(landed="nd_landed", model="claude-sonnet-5")
    migration = newest_migration(facts)
    assert migration is not None and migration.model == "claude-sonnet-5"


@unit
def test_a_null_landing_node_falls_through_to_none_the_schema_entry_allowance() -> None:
    # A NULL landed_node_id reads as "the target's entry": current_node_id returns None,
    # which the call sites resolve via `... or graph.entry_node_id`.
    facts = _migrated_facts(landed=None)
    assert current_node_id(facts) is None
    assert derive_chunk_status(facts) is ChunkStatus.READY


@unit
def test_landing_node_is_name_match_else_entry() -> None:
    graph = reify_graph(
        parse_graph_doc(
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
    )
    build = graph.node_by_name("build")
    assert build is not None
    assert landing_node(graph, "build") == build.node_id  # name match
    assert landing_node(graph, "no-such-node") == graph.entry_node_id  # entry fallback
    assert landing_node(graph, None) == graph.entry_node_id


# --------------------------------------------------------------------------- #
# Component — the atomic store write
# --------------------------------------------------------------------------- #


def _artifact(chunk_id: str, node_id: str) -> ArtifactRow:
    return ArtifactRow(
        kind=ArtifactKind.ASSET,
        name="triage-notes",
        data="hand off to delivery",
        repo=None,
        artifact_id="art_mig",
        chunk_id=chunk_id,
        node_id=node_id,
        node_name="build",
        epoch=1,
    )


def _claimed(hub) -> tuple[str, str, str]:  # type: ignore[no-untyped-def]
    """Mint the source graph + a target graph, claim a route. Returns (chunk_id,
    from_node_id, target_graph_id)."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": _SRC_YAML}).status_code == 201
    target = hub.client.post(
        "/api/graphs",
        json={"definition_yaml": _SRC_YAML.replace("name: default-delivery", "name: triage")},
    ).json()
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    node_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return chunk_id, node_id, target["graph_id"]


@component
def test_record_migration_repins_releases_and_persists_artifacts_in_one_write(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id, target_graph_id = _claimed(hub)
    chunks = cast(IWriteChunkRepository, hub.services.chunks)
    pre_migration = hub.services.chunks.get(chunk_id)
    assert pre_migration is not None
    source_graph_id = pre_migration.graph_id

    wrote = chunks.record_migration(
        chunk_id,
        from_node_id=node_id,
        from_graph_id=source_graph_id,
        to_graph_id=target_graph_id,
        landed_node_id="nd_landed",
        choice_name="migrate",
        model="claude-sonnet-5",
        epoch=1,
        at=hub.clock.now(),
        artifacts=[_artifact(chunk_id, node_id)],
    )

    assert wrote is True
    chunk = hub.services.chunks.get(chunk_id)
    assert chunk is not None
    assert chunk.graph_id == target_graph_id  # re-pinned
    assert chunk.model == "claude-sonnet-5"  # model re-pin
    assert hub.services.chunks.route_of(chunk_id) is None  # route released
    # MUST-FIX 1: the submitting node-step's artifact is durable, so it carries to the
    # landing claim's envelope.
    assert any(a.name == "triage-notes" for a in hub.services.chunks.load_artifacts(chunk_id))
    # The migration is its own fact — no transitions row was written for it.
    facts = hub.services.chunks.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 1
    assert chunks.accepted_transition_target(chunk_id, from_node_id=node_id, epoch=1) is None


@component
def test_record_migration_is_idempotent_on_replay(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id, target_graph_id = _claimed(hub)
    chunks = cast(IWriteChunkRepository, hub.services.chunks)
    pre_migration = hub.services.chunks.get(chunk_id)
    assert pre_migration is not None
    source_graph_id = pre_migration.graph_id

    def do_migrate() -> bool:
        return chunks.record_migration(
            chunk_id,
            from_node_id=node_id,
            from_graph_id=source_graph_id,
            to_graph_id=target_graph_id,
            landed_node_id="nd_landed",
            choice_name="migrate",
            model=None,
            epoch=1,
            at=hub.clock.now(),
            artifacts=[_artifact(chunk_id, node_id)],
        )

    assert do_migrate() is True
    assert chunks.accepted_migration(chunk_id, from_node_id=node_id, epoch=1) is True
    # A crash-replay re-enters harmlessly: writes nothing, no duplicate migration or artifact.
    assert do_migrate() is False
    with hub.engine.connect() as conn:
        migrations = conn.execute(select(s.chunk_migrations).where(s.chunk_migrations.c.chunk_id == chunk_id)).all()
        artifacts = conn.execute(select(s.artifacts).where(s.artifacts.c.chunk_id == chunk_id)).all()
    assert len(migrations) == 1
    assert len([a for a in artifacts if a.name == "triage-notes"]) == 1

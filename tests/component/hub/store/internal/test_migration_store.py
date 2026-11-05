"""Cross-graph migration — the store write + derivations.

A :class:`MigrationFact` makes ``current_node_id`` the landing node and status ``ready``;
``record_migration`` re-pins the graph, releases the route, and persists artifacts in one
idempotent write. adds ``IntendedMigration`` and the ``clear_intent`` flag.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from sqlalchemy import select

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.chunk_migration import MigrationMode
from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import (
    IntendedMigration,
    WorkRef,
    decide_close_enqueue,
)
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission, FenceRefusal
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.ports.record import IWriteChunkRecordRepository
from blizzard.hub.store import schema as s
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    chunk_stores,
    hub_scoped,
    make_ready,
    pointer_token,
    report_lease,
)

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

# A target graph whose landing node (name-matches the source's ``build``) is
# hub-executed — a migration landing here must derive ``delivering``.
_HUB_TARGET_YAML = """
name: triage
entry: build
nodes:
  build:
    executor: hub
    action: pass
    judgement:
      choices:
        pass:
          description: done
          to: done
        success:
          description: passed through
          to: build
        failure:
          description: a command failed
          to: build
"""


# --------------------------------------------------------------------------- #
# Component — the atomic store write


def _artifact(chunk_id: str, node_id: str) -> StoredArtifact:
    return StoredArtifact(
        kind=ArtifactKind.ASSET,
        name="triage-notes",
        data="hand off to delivery",
        repo=None,
        forge=None,
        artifact_id="art_mig",
        chunk_id=chunk_id,
        node_id=node_id,
        node_name="build",
        epoch=1,
    )


def _claimed(hub: HubHarness) -> tuple[str, str, str]:
    """Mint the source graph + a target graph, claim a route. Returns (chunk_id,
    from_node_id, target_graph_id)."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": _SRC_YAML}).status_code == 201
    target = hub.client.post(
        "/api/graphs",
        json={"definition_yaml": _SRC_YAML.replace("name: default-delivery", "name: triage")},
    ).json()
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    make_ready(hub, chunk_id)
    node_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return chunk_id, node_id, target["graph_id"]


def test_record_migration_repins_releases_and_persists_artifacts_in_one_write(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id, target_graph_id = _claimed(hub)
    chunks = cast(IWriteChunkMovementRepository, hub.services.chunks.movement)
    pre_migration = hub.services.chunks.record.get(chunk_id)
    assert pre_migration is not None
    source_graph_id = pre_migration.graph_id

    wrote = chunks.record_migration(
        chunk_id,
        close_decision=decide_close_enqueue([WorkRef(**_POINTER)], lambda _: True),
        from_node_id=node_id,
        from_graph_id=source_graph_id,
        to_graph_id=target_graph_id,
        landed_node_id="nd_landed",
        choice_name="migrate",
        model="claude-sonnet-5",
        epoch=1,
        source=MigrationSource.AUTHORED_EDGE,
        at=hub.clock.now(),
        artifacts=[_artifact(chunk_id, node_id)],
        admission=EpochAdmission.AT_OR_ABOVE,
    )

    assert wrote is not None
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    assert chunk.graph_id == target_graph_id  # re-pinned
    # Retargeted the re-pin: the authored choice `model:` is still a single
    # string, and it lands in the prioritized `default_model` list as its one entry.
    assert chunk.default_model == ["claude-sonnet-5"]
    assert hub.services.chunks.route.route_of(chunk_id) is None  # route released
    # MUST-FIX 1: the submitting node-step's artifact is durable.
    assert any(a.name == "triage-notes" for a in hub.services.chunks.artifacts.load_artifacts(chunk_id))
    # The migration is its own fact — no transitions row was written for it.
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 1
    assert chunks.accepted_transition_target(chunk_id, from_node_id=node_id, epoch=1) is None


def test_record_migration_is_idempotent_on_replay(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id, target_graph_id = _claimed(hub)
    chunks = cast(IWriteChunkMovementRepository, hub.services.chunks.movement)
    pre_migration = hub.services.chunks.record.get(chunk_id)
    assert pre_migration is not None
    source_graph_id = pre_migration.graph_id

    def do_migrate() -> str | FenceRefusal | None:
        return chunks.record_migration(
            chunk_id,
            close_decision=decide_close_enqueue([WorkRef(**_POINTER)], lambda _: True),
            from_node_id=node_id,
            from_graph_id=source_graph_id,
            to_graph_id=target_graph_id,
            landed_node_id="nd_landed",
            choice_name="migrate",
            model=None,
            epoch=1,
            source=MigrationSource.AUTHORED_EDGE,
            at=hub.clock.now(),
            artifacts=[_artifact(chunk_id, node_id)],
            admission=EpochAdmission.AT_OR_ABOVE,
        )

    assert do_migrate() is not None
    assert chunks.accepted_migration(chunk_id, from_node_id=node_id, epoch=1) is True
    # A crash-replay re-enters harmlessly: writes nothing, no duplicate migration or artifact.
    assert do_migrate() is None
    with hub.engine.connect() as conn:
        migrations = conn.execute(
            hub_scoped(select(s.chunk_migrations).where(s.chunk_migrations.c.chunk_id == chunk_id))
        ).all()
        artifacts = conn.execute(hub_scoped(select(s.artifacts).where(s.artifacts.c.chunk_id == chunk_id))).all()
    assert len(migrations) == 1
    assert len([a for a in artifacts if a.name == "triage-notes"]) == 1


def test_a_migration_landing_on_a_hub_node_derives_delivering_and_is_not_ready(tmp_path: Path) -> None:
    """A cross-graph migration whose landing node is hub-executed is
    retained by the hub — the chunk must derive ``delivering``, not runner-claimable
    ``ready``, and so must be absent from :meth:`ChunkRecordStore.list_ready`."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _SRC_YAML}).status_code == 201
    target = hub.client.post("/api/graphs", json={"definition_yaml": _HUB_TARGET_YAML}).json()
    target_graph_id = target["graph_id"]
    landed = next(n for n in target["nodes"] if n["name"] == "build")
    assert landed["executor"] == "hub"

    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    hub.promote(chunk_id)
    node_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    pre_migration = hub.services.chunks.record.get(chunk_id)
    assert pre_migration is not None
    source_graph_id = pre_migration.graph_id

    chunks = cast(IWriteChunkMovementRepository, hub.services.chunks.movement)
    wrote = chunks.record_migration(
        chunk_id,
        close_decision=decide_close_enqueue([WorkRef(**_POINTER)], lambda _: True),
        from_node_id=node_id,
        from_graph_id=source_graph_id,
        to_graph_id=target_graph_id,
        landed_node_id=landed["node_id"],
        choice_name="migrate",
        model=None,
        epoch=1,
        source=MigrationSource.AUTHORED_EDGE,
        at=hub.clock.now(),
        artifacts=[_artifact(chunk_id, node_id)],
        admission=EpochAdmission.AT_OR_ABOVE,
    )
    assert wrote is not None

    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    migration = facts.newest_migration()
    assert migration is not None
    assert migration.landed_node_executor is Executor.HUB
    assert facts.status() is ChunkStatus.DELIVERING

    statuses = hub.services.chunks.facts.load_live_statuses()
    ready_ids = {c.chunk_id for c in hub.services.chunks.record.list_ready(statuses=statuses)}
    assert chunk_id not in ready_ids


# --------------------------------------------------------------------------- #
# intended_migration — storage + domain types only


def test_set_intended_migration_sets_overwrites_and_clears(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    make_ready(hub, chunk_id)

    record = cast(IWriteChunkRecordRepository, hub.services.chunks.record)
    pre = hub.services.chunks.record.get(chunk_id)
    assert pre is not None and pre.intended_migration is None  # unset by default

    auto = IntendedMigration(mode=MigrationMode.AUTO, graph_id="gr_target", node_name=None)
    with chunk_stores(hub.engine, hub.clock).exclusive.locked([chunk_id]) as handle:
        record.set_intended_migration_locked(handle, chunk_id, intended=auto)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    assert chunk.intended_migration == auto  # round-trips through the store

    forced = IntendedMigration(mode=MigrationMode.FORCED, graph_id="gr_other", node_name="build")
    with chunk_stores(hub.engine, hub.clock).exclusive.locked([chunk_id]) as handle:
        record.set_intended_migration_locked(handle, chunk_id, intended=forced)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    assert chunk.intended_migration == forced  # overwrite

    with chunk_stores(hub.engine, hub.clock).exclusive.locked([chunk_id]) as handle:
        record.set_intended_migration_locked(handle, chunk_id, intended=None)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    assert chunk.intended_migration is None  # clear


def test_record_migration_with_clear_intent_clears_the_intent_atomically(tmp_path: Path) -> None:
    """``clear_intent=True`` folds the intent's clear into the same write as the fact,
    the re-pin, the artifacts, and the route release — a runner landing."""
    hub = build_hub(tmp_path)
    chunk_id, node_id, target_graph_id = _claimed(hub)
    chunks = cast(IWriteChunkMovementRepository, hub.services.chunks.movement)
    record = cast(IWriteChunkRecordRepository, hub.services.chunks.record)
    pre_migration = hub.services.chunks.record.get(chunk_id)
    assert pre_migration is not None
    source_graph_id = pre_migration.graph_id

    intent = IntendedMigration(mode=MigrationMode.AUTO, graph_id=target_graph_id, node_name=None)
    with chunk_stores(hub.engine, hub.clock).exclusive.locked([chunk_id]) as handle:
        record.set_intended_migration_locked(handle, chunk_id, intended=intent)
    armed = hub.services.chunks.record.get(chunk_id)
    assert armed is not None and armed.intended_migration == intent

    wrote = chunks.record_migration(
        chunk_id,
        close_decision=decide_close_enqueue([WorkRef(**_POINTER)], lambda _: True),
        from_node_id=node_id,
        from_graph_id=source_graph_id,
        to_graph_id=target_graph_id,
        landed_node_id="nd_landed",
        choice_name="migrate",
        model=None,
        epoch=1,
        source=MigrationSource.AUTHORED_EDGE,
        at=hub.clock.now(),
        artifacts=[_artifact(chunk_id, node_id)],
        clear_intent=True,
        admission=EpochAdmission.AT_OR_ABOVE,
    )
    assert wrote is not None

    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    assert chunk.graph_id == target_graph_id  # re-pinned
    assert chunk.intended_migration is None  # intent cleared, atomically with the fact
    assert hub.services.chunks.route.route_of(chunk_id) is None  # released (runner landing default)
    assert any(a.name == "triage-notes" for a in hub.services.chunks.artifacts.load_artifacts(chunk_id))
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    assert len(facts.migrations) == 1


def test_record_migration_without_clear_intent_leaves_a_set_intent_untouched(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, node_id, target_graph_id = _claimed(hub)
    chunks = cast(IWriteChunkMovementRepository, hub.services.chunks.movement)
    record = cast(IWriteChunkRecordRepository, hub.services.chunks.record)
    pre_migration = hub.services.chunks.record.get(chunk_id)
    assert pre_migration is not None
    source_graph_id = pre_migration.graph_id

    intent = IntendedMigration(mode=MigrationMode.FORCED, graph_id=target_graph_id, node_name="build")
    with chunk_stores(hub.engine, hub.clock).exclusive.locked([chunk_id]) as handle:
        record.set_intended_migration_locked(handle, chunk_id, intended=intent)

    wrote = chunks.record_migration(
        chunk_id,
        close_decision=decide_close_enqueue([WorkRef(**_POINTER)], lambda _: True),
        from_node_id=node_id,
        from_graph_id=source_graph_id,
        to_graph_id=target_graph_id,
        landed_node_id="nd_landed",
        choice_name="migrate",
        model=None,
        epoch=1,
        source=MigrationSource.AUTHORED_EDGE,
        at=hub.clock.now(),
        artifacts=[],
        admission=EpochAdmission.AT_OR_ABOVE,
        # clear_intent defaults False — an ordinary #90 authored-choice migration,
        # which carries no intent of its own to clear.
    )
    assert wrote is not None
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    assert chunk.intended_migration == intent


def test_record_migration_with_clear_intent_on_a_hub_landing_retains_the_route(tmp_path: Path) -> None:
    """The intent's clear lands atomically on the ``release_route=False`` hub-landing
    branch too: the route stays retained, the intent still
    clears in the same transaction as the fact."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _SRC_YAML}).status_code == 201
    target = hub.client.post("/api/graphs", json={"definition_yaml": _HUB_TARGET_YAML}).json()
    target_graph_id = target["graph_id"]
    landed = next(n for n in target["nodes"] if n["name"] == "build")
    assert landed["executor"] == "hub"

    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    make_ready(hub, chunk_id)
    node_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    pre_migration = hub.services.chunks.record.get(chunk_id)
    assert pre_migration is not None
    source_graph_id = pre_migration.graph_id

    chunks = cast(IWriteChunkMovementRepository, hub.services.chunks.movement)
    record = cast(IWriteChunkRecordRepository, hub.services.chunks.record)
    intent = IntendedMigration(mode=MigrationMode.FORCED, graph_id=target_graph_id, node_name="build")
    with chunk_stores(hub.engine, hub.clock).exclusive.locked([chunk_id]) as handle:
        record.set_intended_migration_locked(handle, chunk_id, intended=intent)

    wrote = chunks.record_migration(
        chunk_id,
        close_decision=decide_close_enqueue([WorkRef(**_POINTER)], lambda _: True),
        from_node_id=node_id,
        from_graph_id=source_graph_id,
        to_graph_id=target_graph_id,
        landed_node_id=landed["node_id"],
        choice_name="migrate",
        model=None,
        epoch=1,
        source=MigrationSource.AUTHORED_EDGE,
        at=hub.clock.now(),
        artifacts=[],
        release_route=False,
        clear_intent=True,
        admission=EpochAdmission.AT_OR_ABOVE,
    )
    assert wrote is not None

    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    assert chunk.intended_migration is None  # cleared even though the route is retained
    assert hub.services.chunks.route.route_of(chunk_id) is not None  # retained (hub landing)
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    migration = facts.newest_migration()
    assert migration is not None and migration.landed_node_executor is Executor.HUB

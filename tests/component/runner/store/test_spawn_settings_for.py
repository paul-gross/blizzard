"""The plural spawn-settings read answers per ``(lease_id, generation)`` exactly what the singular does, drops a key
that resolves to no generation, and reads a flat number of statements and rows as the key set and a lease's
generation depth grow (component tier)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.store import batching
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.leases.model import NewLease
from tests.support.runner_fakes import SqlAlchemyRunnerStore, make_store
from tests.support.runner_mint import new_mint
from tests.support.scaffolding import count_queries, count_rows_read

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _lease(store: SqlAlchemyRunnerStore, lease_id: str, generations: int) -> None:
    store.record_mint(
        new_mint(
            NewLease(
                lease_id=lease_id,
                chunk_id=f"chunk_{lease_id}",
                graph_id="graph_1",
                node_id="node_1",
                node_name="build",
                epoch=1,
                retries_max=1,
                created_at=_NOW,
            )
        )
    )
    for generation in range(1, generations + 1):
        store.record_spawn(
            lease_id,
            pid=generation,
            process_start_time=str(generation),
            spawned_at=_NOW,
            session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{lease_id}"),
            host_settings_revision=generation * 10,
            runner_settings_revision=generation,
            transcripts_root=f"/root/{lease_id}/{generation}",
        )


def test_the_plural_matches_the_singular_across_a_batch_boundary_and_drops_an_unknown_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(batching, "BATCH_SIZE", 2)
    store = make_store("sqlite://")
    _lease(store, "lease_a", 3)
    _lease(store, "lease_b", 2)
    _lease(store, "lease_c", 1)
    keys = [("lease_a", 1), ("lease_a", 3), ("lease_b", 2), ("lease_c", 1), ("lease_b", 1)]

    answered = store.spawn_settings_for([*keys, ("lease_a", 4), ("missing", 1)])

    assert answered == {key: store.spawn_settings(*key) for key in keys}
    assert answered[("lease_a", 3)].transcripts_root == "/root/lease_a/3"


def test_an_empty_key_set_issues_no_statement() -> None:
    store = make_store("sqlite://")
    assert count_queries(store.engine, lambda: store.spawn_settings_for([])) == 0


def test_the_statement_count_is_flat_across_key_set_sizes() -> None:
    measured: list[int] = []
    for n in (1, 6):
        store = make_store("sqlite://")
        for i in range(n):
            _lease(store, f"lease_{i}", 1)
        keys = [(f"lease_{i}", 1) for i in range(n)]
        measured.append(count_queries(store.engine, lambda store=store, keys=keys: store.spawn_settings_for(keys)))
    assert measured[0] == measured[1] == 1


def test_the_rows_read_are_flat_across_a_lease_s_generation_depth() -> None:
    measured: list[int] = []
    for depth in (1, 6):
        store = make_store("sqlite://")
        _lease(store, "lease_a", depth)
        measured.append(
            count_rows_read(
                store.engine, lambda store=store, depth=depth: store.spawn_settings_for([("lease_a", depth)])
            )
        )
    assert measured[0] == measured[1] == 1

"""``newest_positions`` constrained to the requested pairs (component tier): the newest fact of each requested pair
only, statements bounded by batch, and rows read flat as unrequested cross pairs gain facts
(`bzh:newest-per-key-read`). The other bounded readers' tests live beside the config-seam bulk reads."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.foundation.store import batching as batching_module
from blizzard.hub.store.internal.project_position_store import newest_positions
from blizzard.hub.store.schema import project_repository_positions as positions
from tests.support.position_facts import record_fact
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    count_queries,
    count_rows_read,
    hub_store_connections,
)

_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _newest(hub: HubHarness, pairs: set[tuple[str, str]]) -> dict[tuple[str, str], float | None]:
    connections = hub_store_connections(hub.engine)
    with connections.read("newest_positions") as conn:
        return newest_positions(conn, pairs)


# --- newest_positions ----------------------------------------------------------------------


def test_newest_positions_answers_the_newest_fact_of_each_requested_pair_only(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    record_fact(hub, "pA", "rA", 1.0)
    record_fact(hub, "pA", "rA", 2.0)  # newer fact wins
    record_fact(hub, "pB", "rB", None)  # an explicit unranked position
    record_fact(hub, "pA", "rB", 9.0)  # a cross pair, not requested
    record_fact(hub, "pB", "rA", 9.0)

    found = _newest(hub, {("pA", "rA"), ("pB", "rB"), ("pC", "rC")})

    assert found == {("pA", "rA"): 2.0, ("pB", "rB"): None}
    assert _newest(hub, set()) == {}
    assert count_queries(hub.engine, lambda: _newest(hub, set())) == 0


def test_newest_positions_matches_across_a_batch_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    pairs = {(f"p{i}", f"r{i}") for i in range(5)}
    for i, (project_id, repository_id) in enumerate(sorted(pairs)):
        record_fact(hub, project_id, repository_id, float(i))
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 2)
    assert _newest(hub, pairs) == {pair: float(i) for i, pair in enumerate(sorted(pairs))}


def test_newest_positions_rows_read_stay_flat_as_cross_pairs_gain_facts(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    record_fact(hub, "pA", "rA", 1.0)
    record_fact(hub, "pB", "rB", 2.0)
    pairs = {("pA", "rA"), ("pB", "rB")}
    before = count_rows_read(hub.engine, lambda: _newest(hub, pairs))
    record_fact(hub, "pA", "rB", 3.0)
    record_fact(hub, "pB", "rA", 4.0)
    assert count_rows_read(hub.engine, lambda: _newest(hub, pairs)) == before


def test_newest_positions_is_tenant_scoped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    with hub.engine.begin() as conn:
        conn.execute(
            insert(positions).values(
                project_id="pA", repository_id="rA", position=1.0, set_at=_AT, set_by="x", tenant_id="ten_other"
            )
        )
    assert _newest(hub, {("pA", "rA")}) == {}

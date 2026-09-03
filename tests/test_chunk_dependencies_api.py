"""The declare/release dependency routes over the HTTP surface (issue #456, Phase 3).

Proves the controller wires ``DependencyService`` correctly end to end: both verbs are
``CHUNK_CONTROL``-gated, each refusal answers 409 with a body carrying the ids and status
a caller needs to render it, an id never minted answers 404, and every refusal leaves the
``chunk_dependencies`` table byte-for-byte unchanged."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from blizzard.hub.api import chunk_dependencies as chunk_dependencies_module
from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub, ingest, report_lease

pytestmark = pytest.mark.component

_DEPENDENT = {"source": "default", "ref": "dependent"}
_PREREQUISITE = {"source": "default", "ref": "prereq"}


def _claim(hub: HubHarness, chunk_id: str) -> None:
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    report_lease(hub, chunk_id, epoch=1, seq=1)


def _declare(hub: HubHarness, dependent_id: str, prerequisite_id: str, *, by: str | None = None):  # type: ignore[no-untyped-def]
    body = {"prerequisite_chunk_id": prerequisite_id}
    if by is not None:
        body["by"] = by
    return hub.client.post(f"/api/chunks/{dependent_id}/dependencies", json=body)


def _release(hub: HubHarness, dependent_id: str, prerequisite_id: str, *, by: str | None = None):  # type: ignore[no-untyped-def]
    body = {"prerequisite_chunk_id": prerequisite_id}
    if by is not None:
        body["by"] = by
    return hub.client.post(f"/api/chunks/{dependent_id}/dependencies/release", json=body)


def _strand_by_direct_delete(hub: HubHarness, chunk_id: str) -> None:
    """Insert a ``chunk_deleted`` row directly, bypassing ``DeleteService``'s own standing-prerequisite guard. Once
    delete and group are both dependency-aware, no API path can strand a standing edge onto an ephemeral prerequisite
    any more — this reaches that state the only way left, to prove release/declare still answer the accepted residual
    race (``bzh:invariant-checker``'s ``NoStandingDependencyOntoEphemeralChunk``) the same way."""
    with hub.engine.begin() as conn:
        conn.execute(
            s.chunk_deleted.insert().values(chunk_id=chunk_id, deleted_at=hub.clock.now(), deleted_by="operator")
        )


def _rows(hub: HubHarness) -> list[dict[str, Any]]:
    """Every ``chunk_dependencies`` row, every column — the whole table's state, so a
    refusal that inserted *or* updated a row shows up as an inequality, not just a count
    that happens to match."""
    with hub.engine.connect() as conn:
        rows = conn.execute(select(s.chunk_dependencies).order_by(s.chunk_dependencies.c.dependency_id)).mappings()
        return [dict(row) for row in rows]


def test_declare_stores_the_edge_and_answers_202(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT], promote=False)
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)

    resp = _declare(hub, dependent_id, prerequisite_id, by="user:alice")

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["dependent_chunk_id"] == dependent_id
    assert body["prerequisite_chunk_id"] == prerequisite_id
    assert body["declared_by"] == "user:alice"
    assert body["released_at"] is None
    assert len(_rows(hub)) == 1


def test_release_marks_the_edge_released_and_answers_202(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT], promote=False)
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)
    assert _declare(hub, dependent_id, prerequisite_id).status_code == 202

    resp = _release(hub, dependent_id, prerequisite_id)

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["released_at"] is not None
    assert body["released_by"] == "operator"
    assert len(_rows(hub)) == 1


def test_declare_refuses_a_claimed_dependent_and_writes_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT])
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)
    _claim(hub, dependent_id)
    before = _rows(hub)

    resp = _declare(hub, dependent_id, prerequisite_id)

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["chunk_id"] == dependent_id
    assert body["status"] == "running"
    assert _rows(hub) == before == []


def test_declare_refuses_a_cycle_and_writes_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_a = ingest(hub, [_DEPENDENT], promote=False)
    chunk_b = ingest(hub, [_PREREQUISITE], promote=False)
    assert _declare(hub, chunk_a, chunk_b).status_code == 202
    before = _rows(hub)

    resp = _declare(hub, chunk_b, chunk_a)

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["dependent_chunk_id"] == chunk_b
    assert body["prerequisite_chunk_id"] == chunk_a
    assert _rows(hub) == before
    assert len(before) == 1


def test_declare_refuses_a_self_edge_as_the_trivial_cycle(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_DEPENDENT], promote=False)
    before = _rows(hub)

    resp = _declare(hub, chunk_id, chunk_id)

    assert resp.status_code == 409, resp.text
    assert _rows(hub) == before == []


def test_release_refuses_when_no_edge_stands_and_writes_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT], promote=False)
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)
    before = _rows(hub)

    resp = _release(hub, dependent_id, prerequisite_id)

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["dependent_chunk_id"] == dependent_id
    assert body["prerequisite_chunk_id"] == prerequisite_id
    assert _rows(hub) == before == []


def test_release_of_an_already_released_edge_is_refused_and_the_row_is_untouched(tmp_path: Path) -> None:
    """The released row survives the refusal with its first release's stamp — the case a
    row *count* cannot see, since a second release would rewrite ``released_by`` rather
    than add a row."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT], promote=False)
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)
    assert _declare(hub, dependent_id, prerequisite_id).status_code == 202
    assert _release(hub, dependent_id, prerequisite_id, by="user:bob").status_code == 202
    before = _rows(hub)

    resp = _release(hub, dependent_id, prerequisite_id, by="user:carol")

    assert resp.status_code == 409, resp.text
    assert _rows(hub) == before
    assert before[0]["released_by"] == "user:bob"


def test_release_is_admitted_when_the_prerequisite_was_since_deleted(tmp_path: Path) -> None:
    """Blocked is a held state, never a dead end: the release lever keeps working after
    the prerequisite goes ephemeral, so a standing edge can never strand its dependent."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT], promote=False)
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)
    assert _declare(hub, dependent_id, prerequisite_id).status_code == 202
    _strand_by_direct_delete(hub, prerequisite_id)

    resp = _release(hub, dependent_id, prerequisite_id, by="user:bob")

    assert resp.status_code == 202, resp.text
    assert resp.json()["released_by"] == "user:bob"
    assert _rows(hub)[0]["released_at"] is not None


def test_declare_refuses_an_ephemeral_prerequisite_and_writes_nothing(tmp_path: Path) -> None:
    """A deleted chunk id resolves to nothing on the record seam but is ephemeral, not
    never-minted (issue #456) — 409, not 404."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT], promote=False)
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)
    assert hub.client.request("DELETE", f"/api/chunks/{prerequisite_id}", json={}).status_code == 202
    before = _rows(hub)

    resp = _declare(hub, dependent_id, prerequisite_id)

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["chunk_id"] == prerequisite_id
    assert _rows(hub) == before == []


def test_declare_against_a_never_minted_prerequisite_is_404(tmp_path: Path) -> None:
    """An id naming no chunk at all is 404 — the split this slice adds is between
    *ephemeral* and *never minted*, not a blanket 409."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT], promote=False)
    before = _rows(hub)

    resp = _declare(hub, dependent_id, "ch_never_minted")

    assert resp.status_code == 404, resp.text
    assert _rows(hub) == before == []


def test_declare_against_an_unknown_dependent_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)
    before = _rows(hub)

    resp = _declare(hub, "ch_never_minted", prerequisite_id)

    assert resp.status_code == 404, resp.text
    assert _rows(hub) == before == []


def test_release_against_an_unknown_dependent_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)
    before = _rows(hub)

    resp = _release(hub, "ch_never_minted", prerequisite_id)

    assert resp.status_code == 404, resp.text
    assert _rows(hub) == before == []


def test_declare_against_a_dependent_deleted_between_resolution_and_the_write_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The declare route's second 404 route (round 2): the dependent resolves live, but
    a delete lands before the shared lock, so the controller's own ``except
    ChunkNotFound`` branch must map that to 404, not a 500."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT], promote=False)
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)
    before = _rows(hub)
    real_resolve_prerequisite = chunk_dependencies_module._resolve_prerequisite

    def _delete_dependent_then_resolve(services, chunk_id):  # type: ignore[no-untyped-def]
        assert hub.client.request("DELETE", f"/api/chunks/{dependent_id}", json={}).status_code == 202
        return real_resolve_prerequisite(services, chunk_id)

    monkeypatch.setattr(chunk_dependencies_module, "_resolve_prerequisite", _delete_dependent_then_resolve)

    resp = _declare(hub, dependent_id, prerequisite_id)

    assert resp.status_code == 404, resp.text
    assert _rows(hub) == before == []


def test_declare_is_an_idempotent_no_op_reporting_the_standing_edge(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT], promote=False)
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)
    first = _declare(hub, dependent_id, prerequisite_id)
    assert first.status_code == 202, first.text

    second = _declare(hub, dependent_id, prerequisite_id)

    assert second.status_code == 202, second.text
    assert second.json()["dependency_id"] == first.json()["dependency_id"]
    assert len(_rows(hub)) == 1


def test_declare_of_a_standing_edge_is_idempotent_even_once_the_prerequisite_is_ephemeral(tmp_path: Path) -> None:
    """The shipped idempotency claim is unqualified: a pair that already stands is
    reported back whatever became of the prerequisite since, mirroring the release path.
    Re-declaring must never consult the prerequisite's resolvability once the edge stands."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [_DEPENDENT], promote=False)
    prerequisite_id = ingest(hub, [_PREREQUISITE], promote=False)
    first = _declare(hub, dependent_id, prerequisite_id)
    assert first.status_code == 202, first.text
    _strand_by_direct_delete(hub, prerequisite_id)
    before = _rows(hub)

    resp = _declare(hub, dependent_id, prerequisite_id)

    assert resp.status_code == 202, resp.text
    assert resp.json()["dependency_id"] == first.json()["dependency_id"]
    assert _rows(hub) == before


def test_both_verbs_require_chunk_control(tmp_path: Path) -> None:
    """Reachability plus gating end to end, with no session at all — the permission
    dependency runs ahead of chunk resolution, so no fixture chunk needs seeding. The
    static route-classification table proves the same fact declaratively."""
    hub = build_hub(tmp_path, auth_mode="oauth")

    declare_resp = _declare(hub, "ch_dependent", "ch_prereq")
    release_resp = _release(hub, "ch_dependent", "ch_prereq")

    assert declare_resp.status_code == 401, declare_resp.text
    assert release_resp.status_code == 401, release_resp.text

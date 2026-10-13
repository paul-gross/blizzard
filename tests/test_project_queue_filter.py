"""The queue under projects (component tier).

The matched fleet peek narrows the ready order to the calling runner's stored served projects before it selects, so
another project's head never holds a runner and a runner serving nothing is offered nothing. ``?project=`` on
``GET /api/queue``, ``/api/backlog`` and ``/api/fleet/queue/peek`` selects one project's entries from the whole
tenant's order, at their whole-order positions and never reranked. Every queue entry names its chunk's project."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from tests.support import (
    HubHarness,
    build_hub,
    capture_statements,
    count_queries,
    default_project,
    default_tenant,
    hub_store_connections,
    ingest,
    link_project,
    seed_project,
    seed_runner,
    serve_default_tenant,
)

pytestmark = pytest.mark.component

#: A capability set that satisfies any chunk with no declared harness preference.
_CAPABILITY = [{"harness_id": "claude", "default": True}]

#: Each unmatched read: its path, whether its entries are ready (else not ready), and whether a runner reads it.
_READS = [("/api/queue", True, False), ("/api/backlog", False, False), ("/api/fleet/queue/peek", True, True)]


def _two_project_hub(tmp_path: Path) -> tuple[HubHarness, ProjectId]:
    """A hub whose tenant holds ``default`` and ``beta``, both linked to the fake ``default`` source;
    returns it with ``beta``'s id."""
    hub = build_hub(tmp_path)
    beta = seed_project(hub.engine, "beta")
    link_project(hub.engine, project=beta, source="default")
    return hub, beta


def _ingest(hub: HubHarness, ref: str, project: str, *, promote: bool = True) -> str:
    chunk_id = ingest(hub, [{"source": "default", "ref": ref}], project=project, promote=promote)
    hub.clock.advance(timedelta(seconds=1))
    return chunk_id


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _peek(hub: HubHarness, token: str, **body: Any) -> list[tuple[str, int]]:
    """The matched peek's entries for the runner holding ``token``, as ``(chunk_id, position)``."""
    resp = hub.client.post("/api/fleet/queue/peek", json={"capabilities": _CAPABILITY, **body}, headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    return [(entry["chunk_id"], entry["position"]) for entry in resp.json()["entries"]]


def _entries(hub: HubHarness, path: str, *, runner: bool, **params: Any) -> list[dict[str, Any]]:
    headers = _bearer(seed_runner(hub, "reader")) if runner else {}
    resp = hub.client.get(path, params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["entries"]


def test_hold_never_stalls_a_runner_on_the_head_of_a_project_it_does_not_serve(tmp_path: Path) -> None:
    hub, _ = _two_project_hub(tmp_path)
    foreign = _ingest(hub, "1", "beta")
    own = _ingest(hub, "2", "default")
    serves_default = seed_runner(hub, "r-default", projects=("default",))
    serves_beta = seed_runner(hub, "r-beta", projects=("beta",))

    # The entry keeps its whole-order position: the narrowing selects, it never renumbers.
    assert _peek(hub, serves_default, policy="hold") == [(own, 1)]
    assert _peek(hub, serves_default) == [(own, 1)]
    assert _peek(hub, serves_beta, policy="hold") == [(foreign, 0)]


def test_capability_and_hold_apply_within_the_narrowed_order(tmp_path: Path) -> None:
    hub, _ = _two_project_hub(tmp_path)
    _ingest(hub, "1", "beta")
    incompatible = _ingest(hub, "2", "default", promote=False)
    patched = hub.client.patch(f"/api/chunks/{incompatible}", json={"default_harnesses": ["special-harness"]})
    assert patched.status_code == 202, patched.text
    assert hub.client.post(f"/api/chunks/{incompatible}/promote").status_code == 202
    hub.clock.advance(timedelta(seconds=1))
    workable = _ingest(hub, "3", "default")
    token = seed_runner(hub, "r-default", projects=("default",))

    assert _peek(hub, token, policy="hold") == []
    assert _peek(hub, token) == [(workable, 2)]


def test_a_runner_serving_nothing_is_offered_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _ingest(hub, "1", default_project(hub.engine))
    token = seed_runner(hub, "r-idle", projects=())

    assert _peek(hub, token) == []
    assert _peek(hub, token, policy="hold") == []


def test_the_peek_serves_the_runners_stored_declaration_and_ignores_projects_in_the_body(tmp_path: Path) -> None:
    hub, beta = _two_project_hub(tmp_path)
    _ingest(hub, "1", "beta")
    own = _ingest(hub, "2", "default")
    token = seed_runner(hub, "r-default", projects=("default",))

    claimed = {"project_id": beta, "projects": [beta], "served": [beta]}
    assert _peek(hub, token, **claimed) == [(own, 1)]


def test_a_dependency_on_another_projects_chunk_still_holds_back_its_dependent(tmp_path: Path) -> None:
    hub, _ = _two_project_hub(tmp_path)
    prerequisite = _ingest(hub, "1", "beta")
    dependent = _ingest(hub, "2", "default")
    declared = hub.client.post(f"/api/chunks/{dependent}/dependencies", json={"prerequisite_chunk_id": prerequisite})
    assert declared.status_code == 202, declared.text
    token = seed_runner(hub, "r-default", projects=("default",))

    assert _peek(hub, token) == []


@pytest.mark.parametrize("policy", ["hold", "pass-over"])
def test_matched_peek_reconstitutes_only_served_candidates(tmp_path: Path, policy: str) -> None:
    hub, _ = _two_project_hub(tmp_path)
    _ingest(hub, "foreign-head", "beta")
    served_head = _ingest(hub, "served-head", "default")
    served_next = _ingest(hub, "served-next", "default")
    token = seed_runner(hub, "r-default", projects=("default",))
    assert (
        hub.client.patch(f"/api/chunks/{served_head}", json={"default_harnesses": ["unavailable"]}).status_code == 202
    )

    class CandidateFacts(ChunkFactsStore):
        def __init__(self) -> None:
            super().__init__(hub_store_connections(hub.engine), hub.clock)
            self.requested: list[list[str]] = []

        def movement_facts_for(self, chunk_ids):  # type: ignore[no-untyped-def]
            self.requested.append(list(chunk_ids))
            return super().movement_facts_for(chunk_ids)

    facts = CandidateFacts()
    serve_default_tenant(hub, replace(hub.services, chunks=replace(hub.services.chunks, facts=facts)))

    def measured() -> tuple[list[tuple[str, int]], int]:
        with capture_statements(hub.engine) as statements:
            entries = _peek(hub, token, policy=policy)
        # Whole-order/status reads necessarily grow with the fleet. Count the candidate
        # fact and graph projections separately from those ordering inputs.
        candidates = [(sql, params) for sql, params in statements if "FROM graphs " in sql and "JOIN" not in sql]
        with hub.engine.connect() as conn:
            rows = sum(
                conn.exec_driver_sql(f"SELECT count(*) FROM ({sql})", params).scalar_one() for sql, params in candidates
            )
        return entries, rows

    expected = [] if policy == "hold" else [(served_next, 2)]
    entries, rows = measured()
    assert entries == expected
    assert facts.requested[-1] == ([served_head] if policy == "hold" else [served_head, served_next])
    for i in range(6):
        _ingest(hub, f"foreign-{i}", "beta")
    after, after_rows = measured()
    assert after == expected
    assert after_rows == rows
    assert facts.requested[-1] == ([served_head] if policy == "hold" else [served_head, served_next])


@pytest.mark.parametrize(("path", "ready", "runner"), _READS, ids=[read[0] for read in _READS])
def test_a_project_filter_selects_from_the_whole_order_at_whole_order_positions(
    tmp_path: Path, path: str, ready: bool, runner: bool
) -> None:
    hub, beta = _two_project_hub(tmp_path)
    for n in range(5):
        _ingest(hub, str(n), "beta" if n % 2 else "default", promote=ready)

    whole = _entries(hub, path, runner=runner)
    expected = [(row["chunk_id"], row["position"]) for row in whole if row["project_id"] == beta]
    assert len(expected) == 2
    for named in (beta, "beta"):
        narrowed = _entries(hub, path, runner=runner, project=named)
        assert [(row["chunk_id"], row["position"]) for row in narrowed] == expected
        assert {(row["project_slug"], row["project_name"]) for row in narrowed} == {("beta", "beta")}
    assert {row["project_slug"] for row in whole} == {"default", "beta"}


@pytest.mark.parametrize(("path", "ready", "runner"), _READS, ids=[read[0] for read in _READS])
def test_an_unknown_or_foreign_tenants_project_answers_422(
    tmp_path: Path, path: str, ready: bool, runner: bool
) -> None:
    hub = build_hub(tmp_path)
    _ingest(hub, "1", default_project(hub.engine), promote=ready)
    headers = {"X-Blizzard-Tenant": default_tenant(hub.engine)}
    if runner:
        headers |= _bearer(seed_runner(hub, "reader"))
    other = hub.add_tenant("other")

    for named in ("proj_nonexistent", default_project(hub.engine, other.tenant)):
        resp = hub.client.get(path, params={"project": named}, headers=headers)
        assert resp.status_code == 422, resp.text


def test_a_narrowed_queue_pages_through_its_own_project_only(tmp_path: Path) -> None:
    hub, beta = _two_project_hub(tmp_path)
    for n in range(6):
        _ingest(hub, str(n), "beta" if n % 2 else "default")
    whole = _entries(hub, "/api/queue", runner=False, limit=50)
    expected = [(row["chunk_id"], row["position"]) for row in whole if row["project_id"] == beta]
    assert len(expected) == 3

    paged: list[tuple[str, int]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"project": beta, "limit": 1, **({"cursor": cursor} if cursor else {})}
        resp = hub.client.get("/api/queue", params=params)
        assert resp.status_code == 200, resp.text
        paged += [(row["chunk_id"], row["position"]) for row in resp.json()["entries"]]
        cursor = resp.json()["next_cursor"]
        if cursor is None:
            break
    assert paged == expected


def test_the_matched_peek_and_a_reorder_name_the_entrys_project(tmp_path: Path) -> None:
    hub, beta = _two_project_hub(tmp_path)
    own = _ingest(hub, "1", "beta")
    token = seed_runner(hub, "r-beta", projects=("beta",))

    peeked = hub.client.post("/api/fleet/queue/peek", json={"capabilities": _CAPABILITY}, headers=_bearer(token))
    labels = {"project_id": beta, "project_slug": "beta", "project_name": "beta"}
    assert [{k: e[k] for k in labels} for e in peeked.json()["entries"]] == [labels]
    reordered = hub.client.put("/api/queue", json={"chunk_ids": [own]})
    assert reordered.status_code == 200, reordered.text
    assert [{k: e[k] for k in labels} for e in reordered.json()["entries"]] == [labels]


def test_the_queue_reads_its_page_projects_once_however_many_projects_the_page_spans(tmp_path: Path) -> None:
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    one = build_hub(tmp_path / "one")
    for n in range(4):
        _ingest(one, str(n), default_project(one.engine))
    two, _ = _two_project_hub(tmp_path / "two")
    for n in range(4):
        _ingest(two, str(n), "beta" if n % 2 else "default")

    def listed(hub: HubHarness) -> None:
        assert len(_entries(hub, "/api/queue", runner=False)) == 4

    assert count_queries(one.engine, lambda: listed(one)) == count_queries(two.engine, lambda: listed(two))

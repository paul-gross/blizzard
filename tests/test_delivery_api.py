"""List/detail delivery parity and bounded marker reads across a growing fleet."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import event

from blizzard.hub.store import schema as s
from tests.support import build_hub, count_queries, ingest

pytestmark = pytest.mark.component
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _marker(conn, chunk_id: str, name: str, data: str, index: int) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        s.artifacts.insert().values(
            artifact_id=f"art_delivery_{index}",
            chunk_id=chunk_id,
            node_id="nd_delivery",
            node_name="deliver",
            epoch=1,
            name=name,
            kind="asset",
            data=data,
            produced_at=_NOW,
            seq=index,
        )
    )


def test_delivery_survives_reread_and_partial_multi_repo_land(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    with hub.engine.begin() as conn:
        _marker(
            conn,
            chunk_id,
            "delivery-pr/acme/one",
            '{"repo":"acme/one","number":1,"url":"http://forge/acme/one/pull/1"}',
            1,
        )
        _marker(
            conn,
            chunk_id,
            "delivery-pr/acme/two",
            '{"repo":"acme/two","number":2,"url":"http://forge/acme/two/pull/2"}',
            2,
        )
        _marker(conn, chunk_id, "merged/acme/one", "actual-merge-sha", 3)

    def read() -> tuple[dict, dict]:  # type: ignore[type-arg]
        page = hub.client.get("/api/chunks").json()["chunks"][0]
        detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
        return page, detail

    for _ in range(2):
        page, detail = read()
        for field in ("open_prs", "closed_prs", "landed_repos", "landed", "awaiting_external_merge"):
            assert page[field] == detail[field], field
        assert [p["repo"] for p in page["open_prs"]] == ["acme/two"]
        assert page["landed_repos"] == [
            {
                "repo": "acme/one",
                "commit_hash": "actual-merge-sha",
                "url": "http://forge/acme/one/commit/actual-merge-sha",
            }
        ]
        assert not page["awaiting_external_merge"]


def test_replacement_pr_is_open_and_previous_pr_is_closed_in_list_and_detail(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    with hub.engine.begin() as conn:
        _marker(
            conn,
            chunk_id,
            "delivery-pr/acme/one/3",
            '{"repo":"acme/one","number":3,"url":"http://forge/acme/one/pull/3"}',
            1,
        )
        _marker(
            conn,
            chunk_id,
            "delivery-pr/acme/one/4",
            '{"repo":"acme/one","number":4,"url":"http://forge/acme/one/pull/4"}',
            2,
        )
    page = hub.client.get("/api/chunks").json()["chunks"][0]
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    for view in (page, detail):
        assert [p["number"] for p in view["open_prs"]] == [4]
        assert [p["number"] for p in view["closed_prs"]] == [3]
        assert not view["awaiting_external_merge"]


def test_delivery_marker_read_only_touches_page_ids_at_two_fleet_sizes(tmp_path: Path) -> None:
    counts = []
    for size in (4, 20):
        directory = tmp_path / str(size)
        directory.mkdir()
        hub = build_hub(directory)
        for i in range(size):
            chunk_id = ingest(hub, [{"source": "default", "ref": str(i)}])
            with hub.engine.begin() as conn:
                _marker(conn, chunk_id, "merged/acme/one", f"sha-{i}", i)
        delivery_params: list[tuple] = []  # type: ignore[type-arg]

        def watch(conn, cursor, statement, parameters, context, executemany, *, observed=delivery_params):  # type: ignore[no-untyped-def]
            if "FROM artifacts" in statement and " LIKE " in statement:
                observed.append(tuple(parameters))

        event.listen(hub.engine, "before_cursor_execute", watch)
        try:
            result = []

            def call(current=hub, rows=result) -> None:  # type: ignore[no-untyped-def]
                response = current.client.get("/api/chunks", params={"limit": 2})
                assert response.status_code == 200, response.text
                rows.extend(response.json()["chunks"])

            counts.append(count_queries(hub.engine, call))
        finally:
            event.remove(hub.engine, "before_cursor_execute", watch)
        assert len(result) == 2
        assert len(delivery_params) == 1
        assert sum(value.startswith("ch_") for value in delivery_params[0] if isinstance(value, str)) == 2
        assert len(result[0]["landed_repos"]) == 1
    assert counts[0] == counts[1]

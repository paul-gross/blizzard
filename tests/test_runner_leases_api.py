"""The runner-local active-lease list — ``GET /api/leases``.

Exercised over a real store via TestClient, hub-free. The route's shape, its binding
join, its empty and unwired forms, and the derivation→wire mapping (``parked`` via real
park facts, ``spawning`` via a null pid) are the point.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Unpack

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.activity import LocalLeaseService
from blizzard.runner.leases.model import NewLease
from blizzard.wire.lease import LeaseView
from tests.config_inputs import create_runner_app
from tests.factory_fields import NewLeaseFields
from tests.runner_client import runner_client
from tests.runner_fakes import (
    FakeProbe,
    SqlAlchemyRunnerStore,
    make_host_stores,
    make_store,
    make_stores,
    runner_store_errors,
)
from tests.support import assert_all_timestamps_utc, count_queries

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _app_with_leases(tmp_path: Path, *, clock: FixedClock | None = None, probe: FakeProbe | None = None):
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    service = LocalLeaseService(
        clock or FixedClock(_NOW),
        probe or FakeProbe(),
        lease_record=store,
        liveness=store,
        asks=store,
        environments=store,
        overload=store,
        elicitations=store,
        git_commits=store,
        takeovers=store,
        escalations=store,
    )
    return create_runner_app(
        config, runner_stores=make_stores(store), host_stores=make_host_stores(store), leases=service
    ), store


def _seed_lease(store: SqlAlchemyRunnerStore, **overrides: Unpack[NewLeaseFields]) -> None:
    fields: NewLeaseFields = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_lease(NewLease(**fields))


@pytest.mark.component
def test_empty_store_returns_empty_items_not_an_error(tmp_path: Path) -> None:
    app, _store = _app_with_leases(tmp_path)
    with runner_client(app) as client:
        resp = client.get("/api/leases")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": []}


@pytest.mark.component
def test_503_when_store_and_leases_unwired(tmp_path: Path) -> None:
    """The store-free app (OpenAPI export / unit boot) refuses to serve rather than pretend."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with runner_client(create_runner_app(config)) as client:
        resp = client.get("/api/leases")
    assert resp.status_code == 503


@pytest.mark.component
def test_running_lease_shape_and_binding_join(tmp_path: Path) -> None:
    app, store = _app_with_leases(tmp_path, probe=FakeProbe(alive={(100, "start-100")}))
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    beat_at = _NOW + timedelta(minutes=1)
    store.record_heartbeat(lease_id="lease_1", beat_at=beat_at)

    with runner_client(app) as client:
        resp = client.get("/api/leases")

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    # Timestamps carry an explicit UTC offset: the store column is UtcDateTime-typed
    # (`bzh:utc-instants`), and `_view` serializes with `iso_utc`.
    assert item == {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "session_id": "sess-a",
        "harness_id": "claude_code",
        "pid": 100,
        "environment_id": "e1",
        "workdir": "/ws/e1",
        "created_at": _NOW.isoformat(),
        "last_heartbeat_at": beat_at.isoformat(),
        "state": "running",
        "display_status": "running",
        "project_id": None,
        "closure_healthy": False,
        "closed_at": None,
        "closure_reason": None,
        "stale_after_seconds": 3600,
        "resolved_model": None,
        "resolved_effort": None,
        "harness_version": None,
        "git_commits": [],
    }


@pytest.mark.component
def test_timestamps_serialize_with_an_explicit_utc_offset(tmp_path: Path) -> None:
    """Timestamps carry an explicit ``+00:00`` offset — asserted on the literal serialized
    strings, not just the round-tripped value."""
    app, store = _app_with_leases(tmp_path, probe=FakeProbe(alive={(100, "start-100")}))
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW + timedelta(minutes=1))

    with runner_client(app) as client:
        resp = client.get("/api/leases")

    body = resp.json()
    item = body["items"][0]
    assert item["created_at"] == "2026-07-16T12:00:00+00:00"
    assert item["last_heartbeat_at"] == "2026-07-16T12:01:00+00:00"
    # Every timestamp in the response, via the shared walker.
    assert_all_timestamps_utc(body)

    # Parsing the string recovers the true instant.
    assert datetime.fromisoformat(item["last_heartbeat_at"]) == _NOW + timedelta(minutes=1)


@pytest.mark.component
def test_spawning_state_reaches_the_wire_via_a_null_pid(tmp_path: Path) -> None:
    """Pins the derivation->wire mapping beyond the happy path (watch item #3 sibling)."""
    app, store = _app_with_leases(tmp_path)
    _seed_lease(store)
    # No record_spawn — pid/session_id stay unset, so the lease derives `spawning`.

    with runner_client(app) as client:
        resp = client.get("/api/leases")

    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["state"] == "spawning"
    assert items[0]["pid"] is None
    assert items[0]["session_id"] is None
    assert items[0]["last_heartbeat_at"] is None


@pytest.mark.component
def test_closed_lease_appears_after_active_with_state_and_reason(tmp_path: Path) -> None:
    """The widened route: recently-closed leases join active ones,
    ordered after them, carrying ``state: "closed"`` and the closure reason on the wire."""
    app, store = _app_with_leases(tmp_path, probe=FakeProbe(alive={(100, "start-100")}))
    _seed_lease(store, lease_id="lease_1", chunk_id="ch_1")
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    _seed_lease(store, lease_id="lease_2", chunk_id="ch_2")
    closed_at = _NOW + timedelta(minutes=5)
    store.record_closure(lease_id="lease_2", chunk_id="ch_2", node_id="nd_build", reason="failed", closed_at=closed_at)

    with runner_client(app) as client:
        resp = client.get("/api/leases")

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [item["lease_id"] for item in items] == ["lease_1", "lease_2"]
    active, closed = items
    assert active["state"] == "running"
    assert active["closed_at"] is None
    assert active["closure_reason"] is None
    assert closed["state"] == "closed"
    assert closed["closed_at"] == closed_at.isoformat()
    assert closed["closure_reason"] == "failed"
    assert closed["closure_healthy"] is False
    assert closed["display_status"] == "closed"
    assert closed["stale_after_seconds"] == 3600
    assert_all_timestamps_utc({"items": items})


@pytest.mark.component
def test_a_lease_carries_its_chunks_project_open_and_after_its_binding_is_released(tmp_path: Path) -> None:
    """The project rides the chunk's newest binding that names one; a released binding row is retained, so a
    closed lease still reads its project, and a chunk no binding names one reads ``None``."""
    app, store = _app_with_leases(tmp_path)
    _seed_lease(store, lease_id="lease_open", chunk_id="ch_open")
    store.record_binding(chunk_id="ch_open", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, project_id="proj_a")
    _seed_lease(store, lease_id="lease_closed", chunk_id="ch_closed")
    store.record_binding(chunk_id="ch_closed", environment_id="e2", workdir="/ws/e2", bound_at=_NOW)
    store.record_binding(
        chunk_id="ch_closed", environment_id="e3", workdir="/ws/e3", bound_at=_NOW, project_id="proj_b"
    )
    store.record_release(chunk_id="ch_closed", environment_id="e2", released_at=_NOW + timedelta(minutes=1))
    store.record_release(chunk_id="ch_closed", environment_id="e3", released_at=_NOW + timedelta(minutes=1))
    store.record_closure(
        lease_id="lease_closed", chunk_id="ch_closed", node_id="nd_build", reason="transitioned", closed_at=_NOW
    )
    _seed_lease(store, lease_id="lease_unbound", chunk_id="ch_unbound")

    with runner_client(app) as client:
        items = client.get("/api/leases").json()["items"]

    by_lease = {item["lease_id"]: item for item in items}
    assert by_lease["lease_open"]["project_id"] == "proj_a"
    assert (by_lease["lease_closed"]["state"], by_lease["lease_closed"]["project_id"]) == ("closed", "proj_b")
    assert by_lease["lease_unbound"]["project_id"] is None


@pytest.mark.component
def test_parked_state_reaches_the_wire_via_real_park_facts(tmp_path: Path) -> None:
    """Watch item #3: `parked` driven end to end by a real park fact, not a stubbed boolean."""
    app, store = _app_with_leases(tmp_path, probe=FakeProbe(alive={(100, "start-100")}))
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="q_1", parked_at=_NOW)

    with runner_client(app) as client:
        resp = client.get("/api/leases")

    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["state"] == "parked"


def _spawn(store: SqlAlchemyRunnerStore) -> None:
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )


def _seed_active(store: SqlAlchemyRunnerStore) -> None:
    _spawn(store)


def _seed_parked(store: SqlAlchemyRunnerStore) -> None:
    _spawn(store)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="q_1", parked_at=_NOW)


def _seed_spawning(store: SqlAlchemyRunnerStore) -> None:
    pass


def _seed_closed(store: SqlAlchemyRunnerStore) -> None:
    _spawn(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="failed", closed_at=_NOW)


@pytest.mark.component
@pytest.mark.parametrize(
    ("seed", "now", "state"),
    [
        (_seed_active, _NOW, "running"),
        (_seed_parked, _NOW, "parked"),
        (_seed_spawning, _NOW, "spawning"),
        (_seed_active, _NOW + timedelta(hours=2), "stale"),
        (_seed_closed, _NOW, "closed"),
    ],
)
def test_every_lease_state_serializes_the_same_ordered_keys(tmp_path: Path, seed, now: datetime, state: str) -> None:
    app, store = _app_with_leases(tmp_path, clock=FixedClock(now), probe=FakeProbe(alive={(100, "start-100")}))
    _seed_lease(store)
    seed(store)

    with runner_client(app) as client:
        resp = client.get("/api/leases")

    assert resp.status_code == 200, resp.text
    (item,) = resp.json()["items"]
    assert item["state"] == state
    assert list(item) == list(LeaseView.model_fields)


@pytest.mark.component
def test_a_transitioned_closure_is_healthy_and_a_failed_one_is_not(tmp_path: Path) -> None:
    app, store = _app_with_leases(tmp_path)
    for n, reason in enumerate(["transitioned", "failed"], start=1):
        _seed_lease(store, lease_id=f"lease_{n}", chunk_id=f"ch_{n}")
        store.record_closure(
            lease_id=f"lease_{n}", chunk_id=f"ch_{n}", node_id="nd_build", reason=reason, closed_at=_NOW
        )

    with runner_client(app) as client:
        items = {item["closure_reason"]: item for item in client.get("/api/leases").json()["items"]}

    assert items["transitioned"]["closure_healthy"] is True
    assert items["failed"]["closure_healthy"] is False


@pytest.mark.component
def test_an_open_ask_on_one_chunk_changes_only_that_chunk_s_display_status(tmp_path: Path) -> None:
    app, store = _app_with_leases(tmp_path, probe=FakeProbe(alive={(100, "start-100"), (200, "start-200")}))
    for n, pid in ((1, 100), (2, 200)):
        _seed_lease(store, lease_id=f"lease_{n}", chunk_id=f"ch_{n}")
        store.record_spawn(
            f"lease_{n}",
            pid=pid,
            process_start_time=f"start-{pid}",
            session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{n}"),
            spawned_at=_NOW,
        )
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="q_1",
        question="which way?",
        options=[],
        asked_at=_NOW,
    )

    with runner_client(app) as client:
        items = {item["chunk_id"]: item for item in client.get("/api/leases").json()["items"]}

    assert items["ch_1"]["display_status"] == "waiting-ask"
    assert items["ch_2"]["display_status"] == "running"
    # The lease's own state is untouched: the human fact outranks it only in the display.
    assert items["ch_1"]["state"] == "running"


def _seed_chunk_history(
    store: SqlAlchemyRunnerStore, *, count: int, chunk_id: str = "ch_1", prefix: str = "lease"
) -> list[str]:
    """``count`` leases of ``chunk_id``, minted a minute apart, every one but the newest closed."""
    ids = [f"{prefix}_{n:02d}" for n in range(count)]
    for n, lease_id in enumerate(ids):
        _seed_lease(store, lease_id=lease_id, chunk_id=chunk_id, epoch=n + 1, created_at=_NOW + timedelta(minutes=n))
        if n < count - 1:
            store.record_closure(
                lease_id=lease_id,
                chunk_id=chunk_id,
                node_id="nd_build",
                reason="failed",
                closed_at=_NOW + timedelta(minutes=n, seconds=30),
            )
    return ids


@pytest.mark.component
def test_the_chunk_filter_serves_every_lease_of_the_chunk_past_twenty_oldest_first(tmp_path: Path) -> None:
    app, store = _app_with_leases(tmp_path)
    ids = _seed_chunk_history(store, count=25)
    _seed_lease(store, lease_id="lease_other", chunk_id="ch_2", epoch=1)

    with runner_client(app) as client:
        resp = client.get("/api/leases", params={"chunk_id": "ch_1"})
        unfiltered = client.get("/api/leases").json()["items"]

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [item["lease_id"] for item in items] == ids
    assert [item["state"] for item in items] == ["closed"] * 24 + ["spawning"]
    # Without the filter the read is the capped panel list: active leases, then twenty closed.
    assert len(unfiltered) == 2 + 20


@pytest.mark.component
def test_a_chunk_s_leases_carry_their_model_effort_harness_version_and_commits(tmp_path: Path) -> None:
    app, store = _app_with_leases(tmp_path)
    _seed_lease(store, lease_id="lease_1", resolved_model="opus", resolved_effort="high")
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
        harness_version="2.0.1",
    )
    store.record_spawn(
        "lease_1",
        pid=101,
        process_start_time="start-101",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW + timedelta(minutes=1),
        harness_version="2.0.2",
    )
    for minutes, repo, commit in ((1, "toy-api", "aaa"), (2, "toy-api", "bbb"), (3, "toy-web", "ccc")):
        store.record_git_commit_declaration(
            lease_id="lease_1",
            chunk_id="ch_1",
            node_id="nd_build",
            epoch=1,
            environment_id="e1",
            repo=repo,
            branch="feat",
            commit=commit,
            declared_at=_NOW + timedelta(minutes=minutes),
        )
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    _seed_lease(store, lease_id="lease_2", epoch=2, created_at=_NOW + timedelta(minutes=5))

    with runner_client(app) as client:
        filtered = client.get("/api/leases", params={"chunk_id": "ch_1"}).json()["items"]
        unfiltered = {item["lease_id"]: item for item in client.get("/api/leases").json()["items"]}

    closed, fresh = filtered
    assert (closed["resolved_model"], closed["resolved_effort"], closed["harness_version"]) == ("opus", "high", "2.0.2")
    assert closed["git_commits"] == [
        {
            "environment_id": "e1",
            "repo": "toy-api",
            "branch": "feat",
            "commit": "bbb",
            "declared_at": (_NOW + timedelta(minutes=2)).isoformat(),
        },
        {
            "environment_id": "e1",
            "repo": "toy-web",
            "branch": "feat",
            "commit": "ccc",
            "declared_at": (_NOW + timedelta(minutes=3)).isoformat(),
        },
    ]
    assert (fresh["resolved_model"], fresh["harness_version"], fresh["git_commits"]) == (None, None, [])
    # The unfiltered read fills the same fields.
    assert unfiltered["lease_1"] == closed
    assert_all_timestamps_utc({"items": filtered})


@pytest.mark.component
def test_another_runner_s_leases_of_the_same_chunk_are_absent(tmp_path: Path) -> None:
    app, store = _app_with_leases(tmp_path)
    _seed_lease(store, lease_id="lease_mine")
    other = SqlAlchemyRunnerStore(store._engine, runner_store_errors(), "runner_other")
    _seed_lease(other, lease_id="lease_theirs", epoch=2)
    other.record_git_commit_declaration(
        lease_id="lease_mine",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        environment_id="e1",
        repo="toy-api",
        branch="feat",
        commit="zzz",
        declared_at=_NOW,
    )

    with runner_client(app) as client:
        items = client.get("/api/leases", params={"chunk_id": "ch_1"}).json()["items"]

    assert [(item["lease_id"], item["git_commits"]) for item in items] == [("lease_mine", [])]


@pytest.mark.component
def test_an_unknown_runner_s_chunk_lease_read_is_refused(tmp_path: Path) -> None:
    app, store = _app_with_leases(tmp_path)
    _seed_lease(store)

    with TestClient(app) as client:
        resp = client.get("/runners/runner_unknown/api/leases", params={"chunk_id": "ch_1"})

    assert resp.status_code == 404
    assert "lease_1" not in resp.text


@pytest.mark.component
@pytest.mark.parametrize("chunk_filter", [True, False])
def test_the_lease_read_s_statement_count_is_flat_in_the_lease_count(tmp_path: Path, chunk_filter: bool) -> None:
    def statements(size: int) -> int:
        (tmp_path / str(size)).mkdir()
        app, store = _app_with_leases(tmp_path / str(size))
        _seed_chunk_history(store, count=size)
        for lease_id in [f"lease_{n:02d}" for n in range(size)]:
            store.record_spawn(
                lease_id,
                pid=100,
                process_start_time="start-100",
                session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
                spawned_at=_NOW,
                harness_version="2.0.1",
            )
            store.record_git_commit_declaration(
                lease_id=lease_id,
                chunk_id="ch_1",
                node_id="nd_build",
                epoch=1,
                environment_id="e1",
                repo="toy-api",
                branch="feat",
                commit="aaa",
                declared_at=_NOW,
            )
        params = {"chunk_id": "ch_1"} if chunk_filter else {}
        with runner_client(app) as client:
            return count_queries(store._engine, lambda: client.get("/api/leases", params=params).raise_for_status())

    assert statements(3) == statements(15)

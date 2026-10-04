"""Finding exit routes against a finding's state (component tier): a verb the model
refuses from a state is 409 and writes nothing, a duplicate id is 422, and the legal
cells the table keeps — reopen from gone or delivered, a person's exit on a delivered
finding, supersede across scopes — still land."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from tests.support import build_hub, hub_store_connections

pytestmark = pytest.mark.component


def _seed_finding(hub, finding_id: str, *, scope: str = "blizzard") -> None:  # type: ignore[no-untyped-def]
    with hub.engine.begin() as conn:
        if conn.execute(s.scopes.select().where(s.scopes.c.slug == scope)).first() is None:
            conn.execute(insert(s.scopes).values(slug=scope, description=scope, created_at=hub.clock.now()))
    FindingStore(hub_store_connections(hub.engine)).add(
        finding_id,
        routine_name="nightly",
        scope_slug=scope,
        class_="c",
        locus="a.py:1",
        summary="s",
        introduced=None,
        at=hub.clock.now() - timedelta(hours=2),
    )


def _record(hub, finding_id: str, kind: str, *, actor: str | None = None) -> None:  # type: ignore[no-untyped-def]
    FindingStore(hub_store_connections(hub.engine)).record_fact(
        finding_id, kind=kind, at=hub.clock.now() - timedelta(hours=1), note="earlier", actor=actor
    )


def _state(hub, finding_id: str) -> str:  # type: ignore[no-untyped-def]
    return hub.client.get(f"/api/findings/{finding_id}").json()["state"]


@pytest.mark.parametrize("verb", ["resolve", "confirm-gone", "wont-fix", "not-a-finding"])
def test_an_exit_on_an_exited_finding_is_409_and_writes_nothing(tmp_path: Path, verb: str) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")
    _seed_finding(hub, "fin_2")
    _record(hub, "fin_2", "resolved")

    resp = hub.client.post(f"/api/findings/{verb}", json={"finding_ids": ["fin_1", "fin_2"], "note": "again"})

    assert resp.status_code == 409, resp.text
    assert "fin_2" in resp.json()["detail"]
    assert (_state(hub, "fin_1"), _state(hub, "fin_2")) == ("live", "resolved")


def test_supersede_of_an_exited_finding_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")
    _seed_finding(hub, "fin_2")
    _record(hub, "fin_1", "wont-fix")

    resp = hub.client.post(
        "/api/findings/supersede", json={"finding_ids": ["fin_1"], "note": "n", "superseded_by": "fin_2"}
    )

    assert resp.status_code == 409, resp.text
    assert _state(hub, "fin_1") == "wont-fix"


def test_reopen_on_a_live_finding_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")

    resp = hub.client.post("/api/findings/reopen", json={"finding_ids": ["fin_1"], "note": "n"})

    assert resp.status_code == 409, resp.text
    facts = hub.client.get("/api/findings/fin_1").json()["facts"]
    assert [f["kind"] for f in facts] == ["add"]


@pytest.mark.parametrize("state", ["gone", "delivered"])
def test_reopen_from_gone_or_delivered_lands_live(tmp_path: Path, state: str) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")
    _record(hub, "fin_1", state, actor="closer" if state == "delivered" else None)

    resp = hub.client.post("/api/findings/reopen", json={"finding_ids": ["fin_1"], "note": "still there"})

    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["state"] == "live"


def test_a_persons_exit_on_a_delivered_finding_lands(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")
    _record(hub, "fin_1", "delivered", actor="closer")

    resp = hub.client.post("/api/findings/wont-fix", json={"finding_ids": ["fin_1"], "note": "not worth it"})

    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["state"] == "wont-fix"


@pytest.mark.parametrize("verb", ["resolve", "reopen"])
def test_a_duplicate_id_is_422_and_writes_nothing(tmp_path: Path, verb: str) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")
    if verb == "reopen":
        _record(hub, "fin_1", "wont-fix")
    before = _state(hub, "fin_1")

    resp = hub.client.post(f"/api/findings/{verb}", json={"finding_ids": ["fin_1", "fin_1"], "note": "n"})

    assert resp.status_code == 422, resp.text
    assert "more than once" in resp.json()["detail"]
    assert _state(hub, "fin_1") == before


def test_supersede_naming_an_unknown_id_as_itself_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post(
        "/api/findings/supersede", json={"finding_ids": ["fin_ghost"], "note": "n", "superseded_by": "fin_ghost"}
    )

    assert resp.status_code == 404, resp.text


def test_supersede_into_a_live_finding_in_another_scope_lands(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1", scope="blizzard")
    _seed_finding(hub, "fin_2", scope="other")

    resp = hub.client.post(
        "/api/findings/supersede", json={"finding_ids": ["fin_1"], "note": "n", "superseded_by": "fin_2"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["state"] == "superseded"

"""Finding routes — the read half (blizzard#390) and the human-driven exit verbs
(blizzard#394 Phase 2), component tier.

The read half proves a routine's live findings under one scope, and nothing else, seeded
through ``FindingStore``. The exit routes prove each verb is reachable, refuses a missing
note, and exits many findings in one call, read back through ``GET /findings/{id}``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from tests.support import build_hub, hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _seed_scope(hub, slug: str) -> None:  # type: ignore[no-untyped-def]
    with hub.engine.begin() as conn:
        conn.execute(insert(s.scopes).values(slug=slug, description="", created_at=_NOW))


def test_list_returns_a_routines_live_findings_under_one_scope_and_nothing_else(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, "blizzard")
    _seed_scope(hub, "other-scope")
    findings = FindingStore(hub_store_connections(hub.engine))
    findings.add(
        "fin_1",
        routine_name="nightly",
        scope_slug="blizzard",
        class_="stale-docstring",
        locus="a.py:1",
        summary="s1",
        introduced=None,
        at=_NOW,
    )
    # Same routine, different scope — must not appear.
    findings.add(
        "fin_2",
        routine_name="nightly",
        scope_slug="other-scope",
        class_="stale-docstring",
        locus="b.py:2",
        summary="s2",
        introduced=None,
        at=_NOW,
    )
    # Same scope, different routine — must not appear.
    findings.add(
        "fin_3",
        routine_name="weekly",
        scope_slug="blizzard",
        class_="stale-docstring",
        locus="c.py:3",
        summary="s3",
        introduced=None,
        at=_NOW,
    )
    # Same routine and scope, but gone — excluded unless include_gone.
    findings.add(
        "fin_4",
        routine_name="nightly",
        scope_slug="blizzard",
        class_="dead-code",
        locus="d.py:4",
        summary="s4",
        introduced=None,
        at=_NOW,
    )
    findings.record_fact("fin_4", kind="gone", at=_NOW, note="no longer reproduces")

    resp = hub.client.get("/api/findings", params={"routine": "nightly", "scope": "blizzard"})

    assert resp.status_code == 200, resp.text
    assert [row["finding_id"] for row in resp.json()] == ["fin_1"]

    resp = hub.client.get("/api/findings", params={"routine": "nightly", "scope": "blizzard", "include_gone": True})
    assert {row["finding_id"] for row in resp.json()} == {"fin_1", "fin_4"}


def test_get_renders_one_finding(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, "blizzard")
    FindingStore(hub_store_connections(hub.engine)).add(
        "fin_1",
        routine_name="nightly",
        scope_slug="blizzard",
        class_="stale-docstring",
        locus="a.py:1",
        summary="s1",
        introduced="a1b2c3d",
        at=_NOW,
    )

    resp = hub.client.get("/api/findings/fin_1")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["class"] == "stale-docstring"
    assert body["locus"] == "a.py:1"
    assert body["introduced"] == "a1b2c3d"
    assert body["live"] is True
    assert body["observed_count"] == 0


def test_get_unknown_id_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/findings/fin_ghost")

    assert resp.status_code == 404, resp.text


def _seeded_scopes(hub) -> set[str]:  # type: ignore[no-untyped-def]
    with hub.engine.connect() as conn:
        return {row.slug for row in conn.execute(s.scopes.select())}


def _seed_finding(hub, finding_id: str, *, scope: str = "blizzard") -> None:  # type: ignore[no-untyped-def]
    # `at=hub.clock.now()`, not module `_NOW`: exit-route facts stamp from `hub.clock`, and
    # newest-fact-wins would let a later `_NOW` add outrank a same-instant exit fact.
    if scope not in _seeded_scopes(hub):
        _seed_scope(hub, scope)
    FindingStore(hub_store_connections(hub.engine)).add(
        finding_id,
        routine_name="nightly",
        scope_slug=scope,
        class_="stale-docstring",
        locus="a.py:1",
        summary="s",
        introduced=None,
        at=hub.clock.now(),
    )


def test_resolve_exits_many_findings_in_one_call_and_reads_back(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")
    _seed_finding(hub, "fin_2")

    resp = hub.client.post("/api/findings/resolve", json={"finding_ids": ["fin_1", "fin_2"], "note": "shipped it"})

    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert {row["finding_id"] for row in rows} == {"fin_1", "fin_2"}
    for row in rows:
        assert row["state"] == "resolved"
        assert row["live"] is False
        assert row["note"] == "shipped it"

    fetched = hub.client.get("/api/findings/fin_1").json()
    assert fetched["state"] == "resolved"
    assert fetched["note"] == "shipped it"


def test_resolve_with_a_blank_note_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")

    resp = hub.client.post("/api/findings/resolve", json={"finding_ids": ["fin_1"], "note": "   "})

    assert resp.status_code == 422, resp.text
    assert hub.client.get("/api/findings/fin_1").json()["state"] == "live"


def test_resolve_an_unknown_finding_id_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/findings/resolve", json={"finding_ids": ["fin_ghost"], "note": "n"})

    assert resp.status_code == 404, resp.text


def test_confirm_gone_records_gone_confirmed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")

    resp = hub.client.post("/api/findings/confirm-gone", json={"finding_ids": ["fin_1"], "note": "checked by hand"})

    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["state"] == "gone-confirmed"


def test_wont_fix_records_wont_fix(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")

    resp = hub.client.post("/api/findings/wont-fix", json={"finding_ids": ["fin_1"], "note": "accepted risk"})

    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["state"] == "wont-fix"


def test_not_a_finding_records_not_a_finding(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")

    resp = hub.client.post("/api/findings/not-a-finding", json={"finding_ids": ["fin_1"], "note": "false positive"})

    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["state"] == "not-a-finding"


def test_supersede_records_superseded_and_names_the_absorbing_finding(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")
    _seed_finding(hub, "fin_2")

    resp = hub.client.post(
        "/api/findings/supersede",
        json={"finding_ids": ["fin_1"], "note": "folded into fin_2", "superseded_by": "fin_2"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["state"] == "superseded"


def test_supersede_an_unknown_absorbing_finding_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")

    resp = hub.client.post(
        "/api/findings/supersede",
        json={"finding_ids": ["fin_1"], "note": "n", "superseded_by": "fin_ghost"},
    )

    assert resp.status_code == 404, resp.text
    # Left untouched — the absorbing finding was invalid, so nothing should have exited.
    assert hub.client.get("/api/findings/fin_1").json()["state"] == "live"


def test_supersede_rejects_a_finding_naming_itself_as_the_absorber(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")

    resp = hub.client.post(
        "/api/findings/supersede",
        json={"finding_ids": ["fin_1"], "note": "n", "superseded_by": "fin_1"},
    )

    assert resp.status_code == 422, resp.text
    assert hub.client.get("/api/findings/fin_1").json()["state"] == "live"


def test_supersede_rejects_a_non_live_absorbing_finding(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")
    _seed_finding(hub, "fin_2")
    hub.client.post("/api/findings/wont-fix", json={"finding_ids": ["fin_2"], "note": "meh"})

    resp = hub.client.post(
        "/api/findings/supersede",
        json={"finding_ids": ["fin_1"], "note": "n", "superseded_by": "fin_2"},
    )

    assert resp.status_code == 422, resp.text
    assert hub.client.get("/api/findings/fin_1").json()["state"] == "live"


def test_reopen_undoes_an_exit_and_restores_liveness(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_finding(hub, "fin_1")
    hub.client.post("/api/findings/wont-fix", json={"finding_ids": ["fin_1"], "note": "meh"})

    resp = hub.client.post("/api/findings/reopen", json={"finding_ids": ["fin_1"], "note": "regressed"})

    assert resp.status_code == 200, resp.text
    body = resp.json()[0]
    assert body["state"] == "live"
    assert body["live"] is True
    assert body["note"] == "regressed"

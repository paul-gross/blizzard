"""Finding routes — the read half (blizzard#390, component tier).

Proves the acceptance scenario: a routine's live findings under one scope, and nothing
else — seeded straight through ``FindingStore`` since no route writes a finding yet
(delivery is a sibling issue), the ``tests/test_scope_lifecycle_api.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from tests.support import build_hub

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _seed_scope(hub, slug: str) -> None:  # type: ignore[no-untyped-def]
    with hub.engine.begin() as conn:
        conn.execute(insert(s.scopes).values(slug=slug, description="", created_at=_NOW))


def test_list_returns_a_routines_live_findings_under_one_scope_and_nothing_else(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub, "blizzard")
    _seed_scope(hub, "other-scope")
    findings = FindingStore(hub.engine)
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
    FindingStore(hub.engine).add(
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

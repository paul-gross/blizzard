"""``GET /api/routines/trend`` (blizzard#394 Phase 4, component tier) — a routine's
finding inflow-against-outflow over a window, seeded straight through ``findings``/
``finding_facts`` so each fact's own ``recorded_at``/``introduced_at`` is pinned exactly
(the ``tests/test_finding_api.py`` shape)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.foundation.ids import ROUTINE_PREFIX, Id
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.routines import Routine
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.routine_store import RoutineStore
from tests.support import build_hub, hub_store_connections

pytestmark = pytest.mark.component

_SINCE = datetime(2026, 1, 1, tzinfo=UTC)
_UNTIL = datetime(2026, 1, 15, tzinfo=UTC)


def _seed_scope(hub, slug: str = "blizzard") -> None:  # type: ignore[no-untyped-def]
    with hub.engine.begin() as conn:
        conn.execute(insert(s.scopes).values(slug=slug, description="", created_at=_SINCE))


def _seed_routine(hub, name: str = "nightly", *, default_scope_slug: str = "blizzard") -> None:  # type: ignore[no-untyped-def]
    RoutineStore(hub_store_connections(hub.engine)).create(
        Routine(
            routine_id=Id.mint_at(ROUTINE_PREFIX, _SINCE).value,
            name=name,
            graph_name="g",
            default_scope_slug=default_scope_slug,
            created_at=_SINCE,
        )
    )


def _seed_finding(
    hub,  # type: ignore[no-untyped-def]
    finding_id: str,
    *,
    routine_name: str = "nightly",
    scope_slug: str = "blizzard",
    introduced_at: datetime | None = None,
) -> None:
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.findings).values(
                finding_id=finding_id,
                routine_name=routine_name,
                scope_slug=scope_slug,
                class_="stale-docstring",
                locus="a.py:1",
                summary="s",
                introduced=None,
                introduced_at=introduced_at,
            )
        )


def _seed_fact(hub, finding_id: str, *, kind: str, recorded_at: datetime) -> None:  # type: ignore[no-untyped-def]
    with hub.engine.begin() as conn:
        conn.execute(insert(s.finding_facts).values(finding_id=finding_id, kind=kind, recorded_at=recorded_at))


def _params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "routine": "nightly",
        "since": iso_utc(_SINCE),
        "until": iso_utc(_UNTIL),
        "introduced_boundary": iso_utc(_SINCE),
        "period_days": 7,
    }
    params.update(overrides)
    return params


def test_trend_reports_per_period_created_and_per_kind_exit_counts(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_routine(hub)
    _seed_finding(hub, "fin_1")
    _seed_finding(hub, "fin_2")
    _seed_fact(hub, "fin_1", kind="add", recorded_at=datetime(2026, 1, 2, tzinfo=UTC))
    _seed_fact(hub, "fin_2", kind="add", recorded_at=datetime(2026, 1, 9, tzinfo=UTC))
    _seed_fact(hub, "fin_1", kind="resolved", recorded_at=datetime(2026, 1, 3, tzinfo=UTC))

    resp = hub.client.get("/api/routines/trend", params=_params())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["routine_name"] == "nightly"
    assert len(body["periods"]) == 2
    first, second = body["periods"]
    assert first["created"] == 1
    assert first["exits"]["resolved"] == 1
    assert first["outflow"] == 1
    assert second["created"] == 1
    assert second["exits"]["resolved"] == 0


def test_trend_reports_reopened_on_its_own_not_folded_into_created_or_outflow(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_routine(hub)
    _seed_finding(hub, "fin_1")
    _seed_fact(hub, "fin_1", kind="add", recorded_at=datetime(2026, 1, 2, tzinfo=UTC))
    _seed_fact(hub, "fin_1", kind="resolved", recorded_at=datetime(2026, 1, 3, tzinfo=UTC))
    _seed_fact(hub, "fin_1", kind="reopened", recorded_at=datetime(2026, 1, 4, tzinfo=UTC))
    _seed_fact(hub, "fin_1", kind="resolved", recorded_at=datetime(2026, 1, 5, tzinfo=UTC))

    resp = hub.client.get("/api/routines/trend", params=_params())

    assert resp.status_code == 200, resp.text
    first = resp.json()["periods"][0]
    assert first["created"] == 1
    assert first["outflow"] == 2
    assert first["reopened"] == 1


def test_trend_excludes_withdrawals_from_outflow(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_routine(hub)
    _seed_finding(hub, "fin_1")
    _seed_finding(hub, "fin_2")
    _seed_fact(hub, "fin_1", kind="add", recorded_at=datetime(2026, 1, 2, tzinfo=UTC))
    _seed_fact(hub, "fin_2", kind="add", recorded_at=datetime(2026, 1, 2, tzinfo=UTC))
    _seed_fact(hub, "fin_1", kind="gone-confirmed", recorded_at=datetime(2026, 1, 2, tzinfo=UTC))
    _seed_fact(hub, "fin_2", kind="wont-fix", recorded_at=datetime(2026, 1, 2, tzinfo=UTC))

    body = hub.client.get("/api/routines/trend", params=_params()).json()

    period = body["periods"][0]
    assert period["outflow"] == 1
    assert period["withdrawn"] == 1


def test_trend_age_cut_separates_recent_from_older_and_reports_unattributed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_routine(hub)
    boundary = datetime(2026, 1, 1, tzinfo=UTC)
    _seed_finding(hub, "fin_recent", introduced_at=datetime(2026, 1, 2, tzinfo=UTC))
    _seed_finding(hub, "fin_older", introduced_at=datetime(2025, 12, 1, tzinfo=UTC))
    _seed_finding(hub, "fin_unattributed", introduced_at=None)
    for finding_id in ("fin_recent", "fin_older", "fin_unattributed"):
        _seed_fact(hub, finding_id, kind="add", recorded_at=datetime(2026, 1, 3, tzinfo=UTC))

    body = hub.client.get("/api/routines/trend", params=_params(introduced_boundary=iso_utc(boundary))).json()

    assert body["age"] == {
        "boundary": iso_utc(boundary),
        "recent": 1,
        "older": 1,
        "unattributed": 1,
    }


def test_trend_scopes_to_the_named_routine_only(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_routine(hub)
    _seed_finding(hub, "fin_1", routine_name="nightly")
    _seed_finding(hub, "fin_2", routine_name="other")
    _seed_fact(hub, "fin_1", kind="add", recorded_at=datetime(2026, 1, 2, tzinfo=UTC))
    _seed_fact(hub, "fin_2", kind="add", recorded_at=datetime(2026, 1, 2, tzinfo=UTC))

    body = hub.client.get("/api/routines/trend", params=_params()).json()

    assert body["periods"][0]["created"] == 1


def test_trend_rejects_a_malformed_since(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_routine(hub)

    resp = hub.client.get("/api/routines/trend", params=_params(since="not-a-timestamp"))

    assert resp.status_code == 422, resp.text
    assert "since" in resp.json()["detail"]


def test_trend_rejects_a_non_positive_period_days(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_routine(hub)

    resp = hub.client.get("/api/routines/trend", params=_params(period_days=0))

    assert resp.status_code == 422, resp.text


def test_trend_rejects_an_until_not_after_since(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_routine(hub)

    resp = hub.client.get("/api/routines/trend", params=_params(since=iso_utc(_UNTIL), until=iso_utc(_SINCE)))

    assert resp.status_code == 422, resp.text
    assert "until must be after since" in resp.json()["detail"]


def test_trend_rejects_a_span_bucketing_past_the_period_cap(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_routine(hub)

    resp = hub.client.get(
        "/api/routines/trend",
        params=_params(until=iso_utc(_SINCE + timedelta(days=367)), period_days=1),
    )

    assert resp.status_code == 422, resp.text
    assert "366" in resp.json()["detail"]


def test_trend_404s_on_an_unknown_routine_name(tmp_path: Path) -> None:
    """blizzard#394 review F4: an unresolved routine name must not read as a genuinely
    quiet window — `services.routines` is resolved at the edge before the read, the
    sibling routine routes' own 404 shape."""
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/routines/trend", params=_params(routine="ghost"))

    assert resp.status_code == 404, resp.text
    assert "ghost" in resp.json()["detail"]

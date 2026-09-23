"""``GET /api/routines/proposal-counts`` (blizzard#547, component tier) — garden-proposal
counts per routine and class over a window, split into open/passed/accepted-with-item/
accepted-without-item. Seeded straight through ``GardenProposalStore``/
``insert_garden_proposal_closure_row`` so each proposal's ``created_at`` and closure are
pinned exactly (the ``tests/test_garden_trend_api.py`` shape)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.foundation.ids import ROUTINE_PREFIX, Id
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.garden_proposal_closure import GardenProposalClosureKind, GardenProposalItemOutcome
from blizzard.hub.domain.routines import Routine
from blizzard.hub.domain.work import WorkRef
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.garden_proposal_closure_store import insert_garden_proposal_closure_row
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
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


def _seed_finding(hub, finding_id: str, *, routine_name: str = "nightly", scope_slug: str = "blizzard") -> None:  # type: ignore[no-untyped-def]
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
                introduced_at=None,
            )
        )


def _seed_proposal(
    hub,  # type: ignore[no-untyped-def]
    proposal_id: str,
    *,
    routine_name: str = "nightly",
    class_: str = "fix-the-source",
    finding_id: str,
    at: datetime,
) -> None:
    GardenProposalStore(hub_store_connections(hub.engine)).create(
        proposal_id, routine_name=routine_name, class_=class_, title="t", body="b", findings=[finding_id], at=at
    )


def _pass(hub, proposal_id: str, *, at: datetime) -> None:  # type: ignore[no-untyped-def]
    with hub_store_connections(hub.engine).write("seed_pass") as conn:
        insert_garden_proposal_closure_row(
            conn,
            proposal_id=proposal_id,
            closure=GardenProposalClosureKind.PASSED,
            reason="not worth it",
            closed_by="operator",
            at=at,
            item_outcome=None,
            pointer=None,
        )


def _accept_mint(hub, proposal_id: str, *, at: datetime) -> None:  # type: ignore[no-untyped-def]
    with hub_store_connections(hub.engine).write("seed_accept_mint") as conn:
        insert_garden_proposal_closure_row(
            conn,
            proposal_id=proposal_id,
            closure=GardenProposalClosureKind.ACCEPTED,
            reason=None,
            closed_by="operator",
            at=at,
            item_outcome=GardenProposalItemOutcome.MINTED,
            pointer=WorkRef(source="hub", ref=proposal_id),
        )


def _params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {"since": iso_utc(_SINCE), "until": iso_utc(_UNTIL)}
    params.update(overrides)
    return params


def test_proposal_counts_reports_bucketed_counts_per_routine_and_class(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_routine(hub)
    _seed_finding(hub, "fin_1")
    _seed_finding(hub, "fin_2")
    _seed_proposal(hub, "gprop_open", finding_id="fin_1", at=_SINCE + timedelta(days=1))
    _seed_proposal(hub, "gprop_passed", finding_id="fin_2", at=_SINCE + timedelta(days=2))
    _pass(hub, "gprop_passed", at=_SINCE + timedelta(days=2))

    resp = hub.client.get("/api/routines/proposal-counts", params=_params())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["since"] == iso_utc(_SINCE)
    assert body["until"] == iso_utc(_UNTIL)
    assert body["routine"] is None
    assert body["rows"] == [
        {
            "routine_name": "nightly",
            "class": "fix-the-source",
            "open": 1,
            "passed": 1,
            "accepted_with_item": 0,
            "accepted_without_item": 0,
            "created": 2,
        }
    ]


def test_proposal_counts_filters_to_the_named_routine(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_routine(hub, "nightly")
    _seed_routine(hub, "other")
    _seed_finding(hub, "fin_1", routine_name="nightly")
    _seed_finding(hub, "fin_2", routine_name="other")
    _seed_proposal(hub, "gprop_1", routine_name="nightly", finding_id="fin_1", at=_SINCE + timedelta(days=1))
    _seed_proposal(hub, "gprop_2", routine_name="other", finding_id="fin_2", at=_SINCE + timedelta(days=1))

    resp = hub.client.get("/api/routines/proposal-counts", params=_params(routine="nightly"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["routine"] == "nightly"
    assert [row["routine_name"] for row in body["rows"]] == ["nightly"]


def test_proposal_counts_with_no_filter_returns_every_routine(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_routine(hub, "nightly")
    _seed_routine(hub, "other")
    _seed_finding(hub, "fin_1", routine_name="nightly")
    _seed_finding(hub, "fin_2", routine_name="other")
    _seed_proposal(hub, "gprop_1", routine_name="nightly", finding_id="fin_1", at=_SINCE + timedelta(days=1))
    _seed_proposal(hub, "gprop_2", routine_name="other", finding_id="fin_2", at=_SINCE + timedelta(days=1))

    resp = hub.client.get("/api/routines/proposal-counts", params=_params())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(row["routine_name"] for row in body["rows"]) == ["nightly", "other"]


def test_proposal_counts_404s_on_an_unknown_routine_name(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/routines/proposal-counts", params=_params(routine="ghost"))

    assert resp.status_code == 404, resp.text
    assert "ghost" in resp.json()["detail"]


def test_proposal_counts_rejects_an_until_not_after_since(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/routines/proposal-counts", params=_params(since=iso_utc(_UNTIL), until=iso_utc(_SINCE)))

    assert resp.status_code == 422, resp.text
    assert "until must be after since" in resp.json()["detail"]


def test_proposal_counts_rejects_a_malformed_since(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/routines/proposal-counts", params=_params(since="not-a-timestamp"))

    assert resp.status_code == 422, resp.text
    assert "since" in resp.json()["detail"]


def test_proposal_counts_returns_empty_rows_for_a_known_routine_with_no_proposals_in_window(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_routine(hub)

    resp = hub.client.get("/api/routines/proposal-counts", params=_params(routine="nightly"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["routine"] == "nightly"
    assert body["rows"] == []

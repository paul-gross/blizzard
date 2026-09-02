"""``GET /api/runs`` / ``GET /api/runs/{chunk_id}`` (component tier) — a run's identity
minted through the real routine-run route, delivery/escalation facts seeded directly,
proving the window default, the 404, and the wire shape end to end."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.routines import Routine, RunMode
from blizzard.hub.domain.scopes import ScopeSlug
from blizzard.hub.domain.work import WorkItemAuthor
from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub

pytestmark = pytest.mark.component

_AUTHOR = WorkItemAuthor.user("usr_1")
_NOW = datetime(2026, 7, 1, tzinfo=UTC)


def _default_graph(hub: HubHarness) -> Graph:
    return hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )


def _routine(hub: HubHarness, *, name: str = "gardening", scope: str = "blizzard") -> Routine:
    graph = _default_graph(hub)
    return hub.services.routine_authoring.create(
        name=name,
        graph_name=graph.name,
        default_scope_slug=ScopeSlug.parse(scope),
        default_model=None,
        default_effort=None,
    )


def _run(hub: HubHarness, routine: Routine) -> str:
    result = hub.services.routine_run.run(routine, scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)
    return result.chunk_id


def _seed_delivery(
    hub: HubHarness,
    chunk_id: str,
    finding_set_id: str,
    *,
    artifact_id: str,
    findings: list[dict[str, object]],
    revisions: dict[str, str] | None = None,
    measurement: str | None = None,
    produced_at: datetime = _NOW,
) -> None:
    revisions = revisions or {}
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.artifacts).values(
                artifact_id=artifact_id,
                chunk_id=chunk_id,
                node_id="nd_1",
                node_name="deliver",
                epoch=1,
                name="findings",
                kind="asset",
                data=json.dumps(
                    {"scope": "blizzard", "revisions": revisions, "measurement": measurement, "findings": findings}
                ),
                produced_at=produced_at,
            )
        )
        conn.execute(
            insert(s.finding_sets).values(
                finding_set_id=finding_set_id,
                artifact_id=artifact_id,
                chunk_id=chunk_id,
                scope_slug="blizzard",
                routine_name="gardening",
                revisions=json.dumps(revisions),
                measurement=measurement,
            )
        )


def _seed_escalation(hub: HubHarness, chunk_id: str, *, at: datetime = _NOW) -> None:
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.escalations).values(
                chunk_id=chunk_id,
                epoch=1,
                takeover_command="resume",
                wrapped_takeover_command="wrapped-resume",
                recorded_at=at,
            )
        )


def test_list_runs_reports_a_delivered_run_within_its_default_window(tmp_path: Path) -> None:
    """`since`/`until` default to the last 24 hours ending now — advancing the clock
    past the mint, but well inside 24 hours, proves the default actually applies
    rather than the request happening to name an explicit window."""
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    chunk_id = _run(hub, routine)
    _seed_delivery(hub, chunk_id, "fins_1", artifact_id="art_1", findings=[], revisions={"blizzard": "aaa"})
    hub.clock.advance(timedelta(hours=1))

    resp = hub.client.get("/api/runs")

    assert resp.status_code == 200, resp.text
    (row,) = resp.json()
    assert row["chunk_id"] == chunk_id
    assert row["routine_name"] == "gardening"
    assert row["outcome"] == "ready"
    assert row["escalation"] is None
    (delivered,) = row["delivered"]
    assert delivered["finding_set_id"] == "fins_1"
    assert delivered["revisions"] == {"blizzard": "aaa"}


def test_list_runs_names_the_escalating_node_and_takeover_command(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    chunk_id = _run(hub, routine)
    _seed_escalation(hub, chunk_id)
    hub.clock.advance(timedelta(hours=1))

    resp = hub.client.get("/api/runs")

    (row,) = resp.json()
    assert row["outcome"] == "needs_human"
    assert row["escalation"]["takeover_command"] == "resume"
    assert row["escalation"]["wrapped_takeover_command"] == "wrapped-resume"


def test_list_runs_excludes_a_run_outside_the_explicit_window(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    _run(hub, routine)

    resp = hub.client.get(
        "/api/runs",
        params={"since": iso_utc(datetime(2020, 1, 1, tzinfo=UTC)), "until": iso_utc(datetime(2020, 2, 1, tzinfo=UTC))},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_list_runs_rejects_a_malformed_since(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/runs", params={"since": "not-a-timestamp"})

    assert resp.status_code == 422, resp.text


def test_list_runs_rejects_an_until_not_after_since(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get(
        "/api/runs",
        params={"since": iso_utc(datetime(2026, 2, 1, tzinfo=UTC)), "until": iso_utc(datetime(2026, 1, 1, tzinfo=UTC))},
    )

    assert resp.status_code == 422, resp.text


def test_run_delta_reads_back_the_added_observed_and_gone_groups(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    chunk_id = _run(hub, routine)
    findings = [
        {"op": "add", "class": "stale-docstring", "locus": "a.py:1", "summary": "s", "introduced": None},
        {"op": "observed", "id": "fin_seen"},
        {"op": "gone", "id": "fin_missing", "note": "not found this pass"},
    ]
    _seed_delivery(hub, chunk_id, "fins_1", artifact_id="art_1", findings=findings)
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.findings).values(
                finding_id="fin_new",
                routine_name="gardening",
                scope_slug="blizzard",
                class_="stale-docstring",
                locus="a.py:1",
                summary="s",
                introduced=None,
                introduced_at=None,
            )
        )
        conn.execute(
            insert(s.finding_facts).values(finding_id="fin_new", kind="add", recorded_at=_NOW, finding_set_id="fins_1")
        )

    resp = hub.client.get(f"/api/runs/{chunk_id}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    (set_delta,) = body["sets"]
    (added,) = set_delta["added"]
    assert added["finding_id"] == "fin_new"
    assert added["class"] == "stale-docstring"
    assert set_delta["observed"] == ["fin_seen"]
    (gone,) = set_delta["gone"]
    assert gone["finding_id"] == "fin_missing"


def test_run_delta_404s_on_an_unknown_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/runs/ch_ghost")

    assert resp.status_code == 404, resp.text
    assert "ch_ghost" in resp.json()["detail"]

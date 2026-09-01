"""Hub service tier — a finding's human-driven exits, the delta guard they raise, the
routine trend, and its last-swept table, against a real hub daemon (blizzard#394 Phases
2-4). Both edges are real: raw HTTP, and the shipped ``blizzard hub`` binary as a
subprocess. Findings are minted the way a routine mints them — a run, the mock runner
submitting its ``delta`` over the wire, then the hub's own garden-delivery route — never
ad-hoc SQL. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from blizzard.hub.graphs import PACKAGED
from tests.e2e.test_acceptance_loop import REPO_NAME, _forge, _free_port, _hub
from tests.service.support import (
    mint_fixture,
    mock_runner,
    poll_until,
    require_mock_fleet,
    require_winter_source,
    service_gate,
)

pytestmark = [pytest.mark.service, service_gate]

_ROUTINE = "garden-service"
_SCOPE = "garden-svc"


@dataclass(frozen=True)
class Garden:
    """A real hub daemon with a garden routine seated on it, plus the mock runner that
    submits a run's artifacts and the fixture commit a delta's revisions cite."""

    hub: httpx.Client
    hub_port: int
    runner: httpx.Client
    routine_id: str
    nodes: dict[str, str]
    head: str


@contextlib.contextmanager
def garden_stack(tmp_path: Path) -> Iterator[Garden]:
    bin_dir = require_mock_fleet()
    _workspace, origins, bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    head = subprocess.run(
        ["git", "-C", str(bare), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    forge_port, hub_port = _free_port(), _free_port()
    with (
        _forge(bin_dir, origins, forge_port),
        _hub(tmp_path / "hub", forge_port, hub_port) as hub,
        mock_runner(bin_dir, _free_port(), hub_port) as runner,
    ):
        minted = hub.post(
            "/api/graphs", json={"definition_yaml": yaml.safe_dump(PACKAGED.named("garden-routine").body)}
        )
        assert minted.status_code == 201, minted.text
        graph = hub.get(f"/api/graphs/{minted.json()['graph_id']}")
        assert graph.status_code == 200, graph.text
        nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}

        created = hub.post(
            "/api/routines",
            json={
                "name": _ROUTINE,
                "graph_name": "garden-routine",
                "default_scope_slug": _SCOPE,
                "default_model": [],
                "default_effort": None,
            },
        )
        assert created.status_code == 201, created.text
        assert runner.post("/_drive/register").json()["status"] == 201
        yield Garden(
            hub=hub,
            hub_port=hub_port,
            runner=runner,
            routine_id=created.json()["routine_id"],
            nodes=nodes,
            head=head,
        )


def deliver(
    g: Garden, ops: Sequence[dict[str, Any]], *, scope: str = _SCOPE, proposals: Sequence[dict[str, Any]] = ()
) -> httpx.Response:
    """One routine run's whole delivery: mint the run, claim its entry node as the mock
    runner, submit the run's artifacts over the wire, then post the hub's own
    garden-delivery route. Returns that route's response — ``recorded`` or ``invalid``."""
    run = g.hub.post(f"/api/routines/{g.routine_id}/run", json={"scope_slug": scope, "mode": "full", "note": "sweep"})
    assert run.status_code == 201, run.text
    chunk_id = run.json()["chunk_id"]
    assert g.runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()["claimed"] is True

    delta = {
        "scope": scope,
        "revisions": {REPO_NAME: g.head},
        "measurement": "service sweep",
        "findings": list(ops),
    }
    artifacts = [
        {"name": "survey", "kind": "asset", "content": "{}", "attached": True},
        {"name": "delta", "kind": "asset", "content": json.dumps(delta), "attached": True},
    ]
    if proposals:
        artifacts.append({"name": "docket", "kind": "asset", "content": json.dumps(list(proposals)), "attached": True})
    completed = g.runner.post(
        "/_drive/complete", json={"chunk_id": chunk_id, "choice": "found", "artifacts": artifacts}
    ).json()
    assert completed["response"]["outcome"] == "next", completed

    return g.hub.post(
        f"/api/chunks/{chunk_id}/garden-delivery",
        params={"node_id": g.nodes["deliver"], "epoch": 1},
        json={"delta": ["delta"], "proposals": ["docket"] if proposals else []},
    )


def add_op(locus: str, *, introduced: str | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {"op": "add", "class": "stale-docstring", "locus": locus, "summary": f"weed at {locus}"}
    if introduced is not None:
        op["introduced"] = introduced
    return op


def seed(g: Garden, count: int, *, introduced: str | None = None) -> list[str]:
    """``count`` fresh live findings on the routine, newest-first ids as the read serves
    them — one delivery, one ``add`` per locus."""
    recorded = deliver(g, [add_op(f"src/app.py:{i}", introduced=introduced) for i in range(count)])
    assert recorded.status_code == 200 and recorded.json()["outcome"] == "recorded", recorded.text
    rows = live(g)
    assert len(rows) >= count
    return [r["finding_id"] for r in rows[:count]]


def live(g: Garden) -> list[dict[str, Any]]:
    resp = g.hub.get("/api/findings", params={"routine": _ROUTINE, "scope": _SCOPE})
    assert resp.status_code == 200, resp.text
    return resp.json()


def read_back(g: Garden, finding_id: str) -> dict[str, Any]:
    resp = g.hub.get(f"/api/findings/{finding_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def cli(g: Garden, *args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    """The real ``blizzard`` console script against the running daemon — a subprocess, not
    an in-process runner, so the whole CLI→HTTP path is what answers."""
    proc = subprocess.run(
        [str(Path(sys.executable).parent / "blizzard"), *args],
        env={**os.environ, "BZ_HUB_URL": f"http://127.0.0.1:{g.hub_port}"},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == expect, f"`blizzard {' '.join(args)}` -> {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    return proc


# --- Phase 2 — the exit verbs at both edges --------------------------------- #


def test_every_exit_verb_and_reopen_land_over_real_http(tmp_path: Path) -> None:
    """Each verb's own route records its own fact kind and note, the response carries the
    post-write state, and the same state reads back on a separate GET."""
    with garden_stack(tmp_path) as g:
        a, b, c, d, e, f = seed(g, 6)

        for path, finding_id, expected, body in (
            ("resolve", a, "resolved", {"finding_ids": [a], "note": "the fix landed"}),
            ("confirm-gone", b, "gone-confirmed", {"finding_ids": [b], "note": "no longer reproduces"}),
            ("wont-fix", c, "wont-fix", {"finding_ids": [c], "note": "not worth standing"}),
            ("not-a-finding", d, "not-a-finding", {"finding_ids": [d], "note": "misread the standard"}),
            ("supersede", e, "superseded", {"finding_ids": [e], "note": "folded into f", "superseded_by": f}),
        ):
            resp = g.hub.post(f"/api/findings/{path}", json=body)
            assert resp.status_code == 200, resp.text
            (row,) = resp.json()
            assert (row["finding_id"], row["state"], row["live"], row["note"]) == (
                finding_id,
                expected,
                False,
                body["note"],
            ), row
            assert read_back(g, finding_id)["state"] == expected

        # Only the absorbing finding is left live — five of six have exited.
        assert [r["finding_id"] for r in live(g)] == [f]

        reopened = g.hub.post("/api/findings/reopen", json={"finding_ids": [a], "note": "it came back"})
        assert reopened.status_code == 200, reopened.text
        (row,) = reopened.json()
        assert (row["state"], row["live"], row["note"]) == ("live", True, "it came back"), row
        assert sorted(r["finding_id"] for r in live(g)) == sorted([a, f])


def test_every_exit_verb_and_reopen_land_through_the_real_cli(tmp_path: Path) -> None:
    """The shipped ``blizzard hub finding`` verbs reach the same routes over the network
    and render the post-write state — the second edge Phase 2's criterion names."""
    with garden_stack(tmp_path) as g:
        a, b, c, d, e, f = seed(g, 6)

        for args, finding_id, expected in (
            (["resolve", a, "--note", "the fix landed"], a, "resolved"),
            (["confirm-gone", b, "--note", "no longer reproduces"], b, "gone-confirmed"),
            (["wont-fix", c, "--note", "not worth standing"], c, "wont-fix"),
            (["not-a-finding", d, "--note", "misread the standard"], d, "not-a-finding"),
            (["supersede", e, "--by", f, "--note", "folded into f"], e, "superseded"),
        ):
            out = cli(g, "hub", "finding", *args, "--json")
            (row,) = json.loads(out.stdout)
            assert (row["finding_id"], row["state"], row["live"]) == (finding_id, expected, False), row
            # The daemon agrees with what the CLI printed.
            assert read_back(g, finding_id)["state"] == expected

        # The human-readable render carries the state and the note, not just the id.
        shown = cli(g, "hub", "finding", "show", a).stdout
        assert "resolved" in shown and "the fix landed" in shown, shown

        (row,) = json.loads(cli(g, "hub", "finding", "reopen", a, "--note", "it came back", "--json").stdout)
        assert (row["state"], row["live"]) == ("live", True), row
        listed = json.loads(cli(g, "hub", "finding", "list", "--routine", _ROUTINE, "--scope", _SCOPE, "--json").stdout)
        assert sorted(r["finding_id"] for r in listed) == sorted([a, f])


def test_a_blank_note_is_refused_at_both_edges_and_a_bulk_exit_is_one_call(tmp_path: Path) -> None:
    """D7's note requirement holds on the wire and in the CLI, and one call exits many."""
    with garden_stack(tmp_path) as g:
        a, b, c = seed(g, 3)

        blank = g.hub.post("/api/findings/resolve", json={"finding_ids": [a], "note": "   "})
        assert blank.status_code == 422, blank.text
        missing = g.hub.post("/api/findings/resolve", json={"finding_ids": [a]})
        assert missing.status_code == 422, missing.text
        # Refused before anything was written.
        assert read_back(g, a)["state"] == "live"

        blank_cli = cli(g, "hub", "finding", "resolve", a, "--note", "  ", expect=1)
        assert "note" in (blank_cli.stderr + blank_cli.stdout).lower(), blank_cli.stderr
        missing_cli = cli(g, "hub", "finding", "resolve", a, expect=2)
        assert "--note" in missing_cli.stderr, missing_cli.stderr
        assert read_back(g, a)["state"] == "live"

        bulk = g.hub.post(
            "/api/findings/wont-fix", json={"finding_ids": [a, b, c], "note": "one decision, three weeds"}
        )
        assert bulk.status_code == 200, bulk.text
        rows = bulk.json()
        assert [r["finding_id"] for r in rows] == [a, b, c]
        assert {(r["state"], r["note"]) for r in rows} == {("wont-fix", "one decision, three weeds")}
        assert live(g) == []


# --- Phase 1 D3 — an exit is terminal to a later run's delta ops ------------- #


def test_an_exited_finding_is_no_longer_a_delta_target_but_a_gone_flagged_one_still_is(tmp_path: Path) -> None:
    """D3: a human exit takes the finding out of a run's reach, while the ``gone`` flag a
    run itself raises does not — the flagged finding is still a live delta target."""
    with garden_stack(tmp_path) as g:
        exited, flagged = seed(g, 2)

        assert (
            g.hub.post("/api/findings/wont-fix", json={"finding_ids": [exited], "note": "declined"}).status_code == 200
        )

        gone = deliver(g, [{"op": "gone", "id": flagged, "note": "did not reproduce this sweep"}])
        assert gone.json() == {"outcome": "recorded", "detail": ""}, gone.text
        assert read_back(g, flagged)["state"] == "gone"

        rejected = deliver(g, [{"op": "observed", "id": exited}])
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["outcome"] == "invalid", rejected.text
        assert exited in rejected.json()["detail"], rejected.text
        assert read_back(g, exited)["state"] == "wont-fix"  # the rejected delivery wrote nothing

        revived = deliver(g, [{"op": "observed", "id": flagged}])
        assert revived.json()["outcome"] == "recorded", revived.text
        assert read_back(g, flagged)["state"] == "live"


# --- Phase 4 — the trend, at both edges -------------------------------------- #


def _trend(g: Garden, *, since: datetime, until: datetime, boundary: datetime, period_days: int = 7) -> dict[str, Any]:
    resp = g.hub.get(
        "/api/routines/trend",
        params={
            "routine": _ROUTINE,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "introduced_boundary": boundary.isoformat(),
            "period_days": period_days,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_the_trend_is_served_over_http_and_through_the_real_cli(tmp_path: Path) -> None:
    """Per-period created/exit counts, the outflow-vs-withdrawn roll-ups, and the D5
    introduced-age cut — the same numbers on both edges."""
    with garden_stack(tmp_path) as g:
        # The window's periods are cut from an instant taken BEFORE anything is recorded,
        # so every fact this test writes lands in the window's own last period.
        t0, t0_local = datetime.now(UTC), datetime.now()

        # Two findings carry an `introduced` commit the mock forge resolves (attributed);
        # one carries none (unattributed) — the three D5 buckets in one window.
        recorded = deliver(
            g,
            [
                add_op("src/app.py:1", introduced=g.head),
                add_op("src/app.py:2", introduced=g.head),
                add_op("src/app.py:3"),
            ],
        )
        assert recorded.json()["outcome"] == "recorded", recorded.text
        rows = live(g)
        by_locus = {r["locus"]: r["finding_id"] for r in rows}
        assert (
            g.hub.post(
                "/api/findings/resolve", json={"finding_ids": [by_locus["src/app.py:1"]], "note": "fixed"}
            ).status_code
            == 200
        )
        assert (
            g.hub.post(
                "/api/findings/confirm-gone", json={"finding_ids": [by_locus["src/app.py:2"]], "note": "gone"}
            ).status_code
            == 200
        )
        assert (
            g.hub.post(
                "/api/findings/wont-fix", json={"finding_ids": [by_locus["src/app.py:3"]], "note": "declined"}
            ).status_code
            == 200
        )

        since, until = t0 - timedelta(days=14), t0 + timedelta(days=1)
        body = _trend(g, since=since, until=until, boundary=t0 - timedelta(hours=1))
        assert body["routine_name"] == _ROUTINE
        assert body["period_days"] == 7
        assert len(body["periods"]) == 3  # 15 days of window cut into 7-day slices
        assert [p["created"] for p in body["periods"]] == [0, 0, 3]
        current = body["periods"][-1]
        assert current["exits"] == {
            "gone-confirmed": 1,
            "not-a-finding": 0,
            "resolved": 1,
            "superseded": 0,
            "wont-fix": 1,
        }
        assert (current["outflow"], current["withdrawn"]) == (2, 1)
        # `introduced` resolved against the fixture commit, authored inside the last hour.
        assert (body["age"]["recent"], body["age"]["older"], body["age"]["unattributed"]) == (2, 0, 1)

        # The same findings fall on the other side of a boundary in the future.
        later = _trend(g, since=since, until=until, boundary=t0 + timedelta(days=1))
        assert (later["age"]["recent"], later["age"]["older"], later["age"]["unattributed"]) == (0, 2, 1)

        # The CLI reaches the same route; its window options are local wall-clock, so the
        # numbers are what is compared, not the echoed instants.
        local = t0_local
        out = cli(
            g,
            "hub",
            "routine",
            "trend",
            _ROUTINE,
            "--since",
            (local - timedelta(days=14)).isoformat(timespec="seconds"),
            "--until",
            (local + timedelta(days=1)).isoformat(timespec="seconds"),
            "--introduced-boundary",
            (local - timedelta(hours=1)).isoformat(timespec="seconds"),
            "--period-days",
            "7",
            "--json",
        )
        from_cli = json.loads(out.stdout)
        assert [p["created"] for p in from_cli["periods"]] == [0, 0, 3]
        assert (from_cli["periods"][-1]["outflow"], from_cli["periods"][-1]["withdrawn"]) == (2, 1)
        assert (from_cli["age"]["recent"], from_cli["age"]["unattributed"]) == (2, 1)

        rendered = cli(
            g,
            "hub",
            "routine",
            "trend",
            _ROUTINE,
            "--since",
            (local - timedelta(days=14)).isoformat(timespec="seconds"),
            "--until",
            (local + timedelta(days=1)).isoformat(timespec="seconds"),
            "--introduced-boundary",
            (local - timedelta(hours=1)).isoformat(timespec="seconds"),
        ).stdout
        assert "outflow=2" in rendered and "withdrawn=1" in rendered, rendered
        assert "recent=2" in rendered and "unattributed=1" in rendered, rendered

        # A non-positive period is the 422 the route names, surfaced by the CLI.
        bad = g.hub.get(
            "/api/routines/trend",
            params={
                "routine": _ROUTINE,
                "since": since.isoformat(),
                "until": until.isoformat(),
                "introduced_boundary": t0.isoformat(),
                "period_days": 0,
            },
        )
        assert bad.status_code == 422, bad.text


# --- Gardening routine panel — the sweeps route, at both edges -------------- #


def test_sweeps_reports_last_swept_across_scopes_and_the_windowed_measurement_series(tmp_path: Path) -> None:
    """Every non-retired scope, including one never swept; a retired scope this routine
    has swept stays listed; the measurement series is cut to the window while
    last-swept is not."""
    with garden_stack(tmp_path) as g:
        t0, t0_local = datetime.now(UTC), datetime.now()
        never_swept, retired_swept = "garden-svc-never", "garden-svc-retired"
        for slug in (never_swept, retired_swept):
            created = g.hub.post("/api/scopes", json={"slug": slug, "description": ""})
            assert created.status_code == 201, created.text

        recorded = deliver(g, [add_op("src/app.py:1")])
        assert recorded.status_code == 200 and recorded.json()["outcome"] == "recorded", recorded.text

        retired_delivery = deliver(g, [add_op("src/app.py:2")], scope=retired_swept)
        assert retired_delivery.status_code == 200, retired_delivery.text
        retired = g.hub.post(f"/api/scopes/{retired_swept}/retire", json={"by": "operator"})
        assert retired.status_code == 202, retired.text

        since, until = t0 - timedelta(days=1), t0 + timedelta(days=1)
        resp = g.hub.get(
            f"/api/routines/{g.routine_id}/sweeps", params={"since": since.isoformat(), "until": until.isoformat()}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["routine_name"] == _ROUTINE

        by_scope = {row["scope_slug"]: row for row in body["last_swept"]}
        assert by_scope[never_swept]["finding_set_id"] is None
        assert by_scope[never_swept]["produced_at"] is None
        assert by_scope[_SCOPE]["finding_set_id"] is not None
        assert by_scope[retired_swept]["finding_set_id"] is not None

        swept_scopes = {m["scope_slug"] for m in body["measurements"]}
        assert swept_scopes == {_SCOPE, retired_swept}
        assert all(m["measurement"] == "service sweep" for m in body["measurements"])

        cli_out = cli(
            g,
            "hub",
            "routine",
            "sweeps",
            _ROUTINE,
            "--since",
            (t0_local - timedelta(days=1)).isoformat(timespec="seconds"),
            "--until",
            (t0_local + timedelta(days=1)).isoformat(timespec="seconds"),
            "--json",
        ).stdout
        from_cli = json.loads(cli_out)
        assert {row["scope_slug"] for row in from_cli["last_swept"]} == {_SCOPE, never_swept, retired_swept}


def test_sweeps_404s_on_an_unknown_routine_id(tmp_path: Path) -> None:
    with garden_stack(tmp_path) as g:
        t0 = datetime.now(UTC)
        resp = g.hub.get(
            "/api/routines/rtn_ghost/sweeps",
            params={"since": (t0 - timedelta(days=1)).isoformat(), "until": (t0 + timedelta(days=1)).isoformat()},
        )
        assert resp.status_code == 404, resp.text


# --- Phase 3 — an accepted proposal's delivered item resolves its findings ---- #


def test_delivering_an_accepted_proposals_minted_item_resolves_its_findings(tmp_path: Path) -> None:
    """The Phase 3 hook, driven live: a proposal delivered by a run, accepted over HTTP
    into a minted hub item, and that item closed by the daemon's own close-intent drain —
    the findings the proposal named come back `resolved`, attributed to it."""
    with garden_stack(tmp_path) as g:
        first, second = seed(g, 2)

        proposed = deliver(
            g,
            [{"op": "observed", "id": first}, {"op": "observed", "id": second}],
            proposals=[
                {
                    "ref": "P1",
                    "class": "handoff",
                    "title": "answer both weeds",
                    "body": "one response covering both findings",
                    "findings": [first, second],
                }
            ],
        )
        assert proposed.json()["outcome"] == "recorded", proposed.text
        (proposal,) = g.hub.get("/api/garden-proposals").json()
        assert proposal["findings"] == [first, second], proposal

        accepted = g.hub.post(f"/api/garden-proposals/{proposal['proposal_id']}/accept", json={"reason": "taking it"})
        assert accepted.status_code == 200, accepted.text
        item_chunk = accepted.json()["chunk_id"]
        closure = accepted.json()["closure"]
        assert closure["item_outcome"] == "minted", closure
        pointer = f"{closure['source']}:{closure['ref']}"
        # Accepting alone changes no finding's state.
        assert read_back(g, first)["state"] == "live"

        # Land the minted item's chunk the way a delivery does — a `merged/` marker, which
        # is what enqueues the close intent the hub's own drain then sweeps.
        detail = g.hub.get(f"/api/chunks/{item_chunk}").json()
        graph = g.hub.get(f"/api/graphs/{detail['graph_id']}").json()
        marked = g.hub.post(
            f"/api/chunks/{item_chunk}/hub-markers",
            params={"node_id": graph["entry_node_id"], "epoch": 1},
            json={"name": f"merged/{REPO_NAME}", "content": "landed"},
        )
        assert marked.status_code == 200, marked.text

        assert poll_until(lambda: read_back(g, first)["state"] == "resolved", timeout=90.0), (
            f"the close drain never resolved the proposal's findings: {read_back(g, first)}"
        )
        for finding_id in (first, second):
            row = read_back(g, finding_id)
            assert row["state"] == "resolved", row
            assert row["note"] == f"resolved by delivery of {pointer}", row

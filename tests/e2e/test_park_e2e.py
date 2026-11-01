"""A parked delivery waits on a person against the live mock forge."""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from tests.e2e.flow_support import flow_runner_config, flow_world, landing_graph_yaml, pinned_chunk, ticking_runner
from tests.e2e.test_flow_turns_e2e import _assert_flow_record_holds, _await, _turns, chunk_detail
from tests.e2e.test_landing_order_e2e import WEB_REPO, WEB_REPO_NAME, _add_web, _landed_order, _order, _terms
from tests.fleet_launchers import REPO, run_forge, run_hub
from tests.fleet_world import REPO_NAME
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("BLIZZARD_E2E") != "1", reason="requires the live mock-fleet stack"),
]


@pytest.mark.parametrize("method", ["merge", "squash", "rebase"])
def test_park_waits_past_node_timeout_then_records_human_merge(tmp_path: Path, method: str) -> None:
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        configured = hub.patch(f"/api/repositories/{REPO_NAME}", json={"landing_policy": "park"})
        assert configured.status_code == 200, configured.text
        graph = yaml.safe_load(landing_graph_yaml("PARK.md", (REPO_NAME,), poll_interval=1, record_findings=False))
        graph["nodes"]["deliver"]["poll_timeout"] = 2
        minted = hub.post("/api/graphs", json={"definition_yaml": yaml.safe_dump(graph, sort_keys=False)})
        assert minted.status_code == 201, minted.text
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "PARK.md", "body": "person merges"})
        assert issue.status_code == 201, issue.text
        created = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert created.status_code == 201, created.text
        chunk = created.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk}/promote").status_code == 202
        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            tick_until(lambda: (chunk_detail(hub, chunk).get("pending") or {}).get("kind") == "awaiting-merge")
            pending = chunk_detail(hub, chunk)["pending"]
            since = datetime.fromisoformat(pending["since"])
            due = datetime.fromisoformat(pending["next_poll_at"])
            assert (due - since).total_seconds() >= 119
            pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "open"})
            assert pulls.status_code == 200 and len(pulls.json()) == 1, pulls.text
            number = pulls.json()[0]["number"]
            changed = hub.patch(f"/api/repositories/{REPO_NAME}", json={"landing_policy": "merge-on-checks"})
            assert changed.status_code == 200, changed.text
            assert _terms(hub_dir, chunk)[REPO_NAME][0] == "park"
            time.sleep(4)
            held = chunk_detail(hub, chunk)
            assert held["status"] == "delivering" and held["pending"]["kind"] == "awaiting-merge", held
            merged = forge.put(f"/repos/{REPO}/pulls/{number}/merge", json={"merge_method": method})
            assert merged.status_code == 200, merged.text
            _await(
                lambda: chunk_detail(hub, chunk)["status"] in {"done", "needs_human", "stopped"},
                "human merge",
                timeout=150,
            )
        detail = chunk_detail(hub, chunk)
        assert (detail["status"], detail["landed"]) == ("done", True), detail
        assert len(detail["landed_repos"]) == 1
        assert detail["landed_repos"][0]["commit_hash"] == merged.json()["sha"]
        assert [(item["from_node_name"], item["choice_name"]) for item in detail["history"]][-1] == (
            "deliver",
            "landed",
        )
        _assert_flow_record_holds(hub_dir)


@pytest.mark.parametrize("requeue", [False, True], ids=["closed", "closed-requeue"])
def test_closed_parked_change_escalates_then_requeue_opens_fresh_change(tmp_path: Path, requeue: bool) -> None:
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        configured = hub.patch(f"/api/repositories/{REPO_NAME}", json={"landing_policy": "park"})
        assert configured.status_code == 200, configured.text
        graph = landing_graph_yaml("CLOSED.md", (REPO_NAME,), poll_interval=1, record_findings=False)
        minted = hub.post("/api/graphs", json={"definition_yaml": graph})
        assert minted.status_code == 201, minted.text
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "CLOSED.md", "body": "person closes"})
        assert issue.status_code == 201, issue.text
        created = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert created.status_code == 201, created.text
        chunk = created.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk}/promote").status_code == 202
        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            tick_until(lambda: (chunk_detail(hub, chunk).get("pending") or {}).get("kind") == "awaiting-merge")
            pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "open"})
            assert pulls.status_code == 200 and len(pulls.json()) == 1, pulls.text
            number = pulls.json()[0]["number"]
            closed = forge.patch(f"/repos/{REPO}/pulls/{number}", json={"state": "closed"})
            assert closed.status_code == 200, closed.text
            _await(lambda: chunk_detail(hub, chunk)["status"] == "needs_human", "closed change escalation", timeout=150)
            detail = chunk_detail(hub, chunk)
            assert detail["escalation"]["cause"] == "change-closed", detail
            assert detail["pending"] is None, detail
            if not requeue:
                _assert_flow_record_holds(hub_dir, settled=False)
                return
            turns = len(_turns(hub_dir, chunk))
            requeued = hub.post(f"/api/chunks/{chunk}/requeues")
            assert requeued.status_code == 202, requeued.text
            tick_until(lambda: len(_turns(hub_dir, chunk)) > turns and _turns(hub_dir, chunk)[-1][1] is not None)
            _await(
                lambda: len(forge.get(f"/repos/{REPO}/pulls", params={"state": "open"}).json()) == 1,
                "replacement parked change",
                timeout=150,
            )
            replacement = forge.get(f"/repos/{REPO}/pulls", params={"state": "open"})
            assert replacement.status_code == 200 and len(replacement.json()) == 1, (
                replacement.text,
                chunk_detail(hub, chunk),
            )
            assert replacement.json()[0]["number"] != number
        _assert_flow_record_holds(hub_dir, settled=False)


@pytest.mark.parametrize("parked_first", [True, False], ids=["library-parked", "consumer-parked"])
def test_parked_repository_holds_landing_order_until_person_merges(tmp_path: Path, parked_first: bool) -> None:
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        _add_web(hub, forge_port)
        parked = WEB_REPO_NAME if parked_first else REPO_NAME
        _order(hub, "default", WEB_REPO_NAME, REPO_NAME)
        assert hub.patch(f"/api/repositories/{parked}", json={"landing_policy": "park"}).status_code == 200
        graph = landing_graph_yaml("ORDER-PARK.md", (REPO_NAME, WEB_REPO_NAME), poll_interval=1, record_findings=False)
        assert hub.post("/api/graphs", json={"definition_yaml": graph}).status_code == 201
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "ORDER-PARK.md", "body": "ordered park"})
        assert issue.status_code == 201, issue.text
        created = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert created.status_code == 201, created.text
        chunk = created.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk}/promote").status_code == 202
        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            tick_until(lambda: (chunk_detail(hub, chunk).get("pending") or {}).get("kind") == "awaiting-merge")
            detail = chunk_detail(hub, chunk)
            assert [row["repo"] for row in detail["landed_repos"]] == ([] if parked_first else [WEB_REPO])
            parked_path = WEB_REPO if parked_first else REPO
            pulls = forge.get(f"/repos/{parked_path}/pulls", params={"state": "open"}).json()
            assert len(pulls) == 1
            other = REPO if parked_first else WEB_REPO
            assert len(forge.get(f"/repos/{other}/pulls", params={"state": "open"}).json()) <= 1
            merged = forge.put(f"/repos/{parked_path}/pulls/{pulls[0]['number']}/merge", json={"merge_method": "merge"})
            assert merged.status_code == 200, merged.text
            _await(lambda: chunk_detail(hub, chunk)["status"] == "done", "ordered parked landing", timeout=155)
        detail = chunk_detail(hub, chunk)
        assert _landed_order(hub_dir, chunk) == [WEB_REPO_NAME, REPO_NAME]
        assert (
            next(row for row in detail["landed_repos"] if row["repo"] == parked_path)["commit_hash"]
            == merged.json()["sha"]
        )
        _assert_flow_record_holds(hub_dir)


def test_second_parked_repository_has_its_own_review_clock(tmp_path: Path) -> None:
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        _add_web(hub, forge_port)
        _order(hub, "default", WEB_REPO_NAME, REPO_NAME)
        for slug in (WEB_REPO_NAME, REPO_NAME):
            configured = hub.patch(
                f"/api/repositories/{slug}", json={"landing_policy": "park", "park_timeout_hours": 1}
            )
            assert configured.status_code == 200, configured.text
        chunk = pinned_chunk(hub, forge, "TWO-PARK-CLOCKS.md", (REPO_NAME, WEB_REPO_NAME))
        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            tick_until(lambda: (chunk_detail(hub, chunk).get("pending") or {}).get("kind") == "awaiting-merge")
            first = forge.get(f"/repos/{WEB_REPO}/pulls", params={"state": "open"}).json()
            assert len(first) == 1
            assert forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json() == []
            merged = forge.put(f"/repos/{WEB_REPO}/pulls/{first[0]['number']}/merge", json={"merge_method": "merge"})
            assert merged.status_code == 200, merged.text
            with sqlite3.connect(hub_dir / "data" / "hub.db") as conn:
                conn.execute(
                    "UPDATE artifacts SET produced_at = ? WHERE chunk_id = ? AND name LIKE ?",
                    ((datetime.now(UTC) - timedelta(hours=2)).isoformat(), chunk, f"delivery-pr/{WEB_REPO}/%"),
                )
                conn.execute(
                    "UPDATE hub_node_poll SET last_polled_at = ?, next_poll_at = NULL "
                    "WHERE chunk_id = ? AND kind = 'awaiting-merge'",
                    ((datetime.now(UTC) - timedelta(minutes=3)).isoformat(), chunk),
                )
            _await(
                lambda: len(forge.get(f"/repos/{REPO}/pulls", params={"state": "open"}).json()) == 1,
                "second parked change",
                timeout=155,
            )
            with sqlite3.connect(hub_dir / "data" / "hub.db") as conn:
                rows = conn.execute(
                    "SELECT name, produced_at FROM artifacts WHERE chunk_id = ? AND name LIKE 'delivery-pr/%'",
                    (chunk,),
                ).fetchall()
                assert len(rows) == 2, rows
                first_since = datetime.fromisoformat(next(at for name, at in rows if WEB_REPO in name))
                second_since = datetime.fromisoformat(next(at for name, at in rows if REPO in name))
                assert second_since.replace(tzinfo=UTC) - first_since > timedelta(hours=1)
            detail = chunk_detail(hub, chunk)
            assert detail["status"] == "delivering" and detail["pending"]["kind"] == "awaiting-merge", detail
            second = forge.get(f"/repos/{REPO}/pulls", params={"state": "open"}).json()
            merged_second = forge.put(
                f"/repos/{REPO}/pulls/{second[0]['number']}/merge", json={"merge_method": "squash"}
            )
            assert merged_second.status_code == 200, merged_second.text
            with sqlite3.connect(hub_dir / "data" / "hub.db") as conn:
                conn.execute(
                    "UPDATE hub_node_poll SET last_polled_at = ?, next_poll_at = NULL "
                    "WHERE chunk_id = ? AND kind = 'awaiting-merge'",
                    ((datetime.now(UTC) - timedelta(minutes=3)).isoformat(), chunk),
                )
            _await(lambda: chunk_detail(hub, chunk)["status"] == "done", "both human merges", timeout=155)
        landed = {row["repo"]: row["commit_hash"] for row in chunk_detail(hub, chunk)["landed_repos"]}
        assert landed == {WEB_REPO: merged.json()["sha"], REPO: merged_second.json()["sha"]}
        _assert_flow_record_holds(hub_dir)


def test_park_timeout_then_person_merge_is_adopted_on_requeue(tmp_path: Path) -> None:
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        configured = hub.patch(
            f"/api/repositories/{REPO_NAME}", json={"landing_policy": "park", "park_timeout_hours": 1}
        )
        assert configured.status_code == 200, configured.text
        chunk = pinned_chunk(hub, forge, "PARK-TIMEOUT.md")
        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            tick_until(lambda: (chunk_detail(hub, chunk).get("pending") or {}).get("kind") == "awaiting-merge")
            first_since = chunk_detail(hub, chunk)["pending"]["since"]
            pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "open"}).json()
            assert len(pulls) == 1
            number = pulls[0]["number"]
            # Move only the durable marker clock; the repository's one-hour review window
            # otherwise makes this boundary impractical to exercise in an e2e run.
            aged = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
            with sqlite3.connect(hub_dir / "data" / "hub.db") as conn:
                conn.execute(
                    "UPDATE artifacts SET produced_at = ? WHERE chunk_id = ? AND name LIKE 'delivery-pr/%'",
                    (aged, chunk),
                )
                conn.execute(
                    "UPDATE hub_node_poll SET last_polled_at = ? WHERE chunk_id = ? AND kind = 'awaiting-merge'",
                    (aged, chunk),
                )
            _await(lambda: chunk_detail(hub, chunk)["status"] == "needs_human", "park timeout", timeout=30)
            detail = chunk_detail(hub, chunk)
            assert detail["escalation"]["cause"] == "park-timeout", detail
            assert detail["pending"] is None, detail
            merged = forge.put(f"/repos/{REPO}/pulls/{number}/merge", json={"merge_method": "squash"})
            assert merged.status_code == 200, merged.text
            requeued = hub.post(f"/api/chunks/{chunk}/requeues")
            assert requeued.status_code == 202, requeued.text
            tick_until(lambda: chunk_detail(hub, chunk)["status"] == "done")
        detail = chunk_detail(hub, chunk)
        assert detail["landed_repos"][0]["commit_hash"] == merged.json()["sha"]
        assert detail["pending"] is None
        assert first_since is not None
        assert len(forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()) == 1
        _assert_flow_record_holds(hub_dir)

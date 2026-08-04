"""The forge-status projection, end to end (issue #179) — scenario 16 of the standing e2e smoke.

Reuses the acceptance loop's live-stack scaffolding (real forge, real hub, real runner
loop, ``mock-claude-code`` façade) with the one work source opted into ``annotate =
true`` and a low ``annotation_interval_seconds``, so the projection's own convergence is
observable within the test's real wall-clock budget rather than the operator default of
120s. Four properties, one minted fixture:

* **The happy path** — ingest shows ``blizzard:ingested``; driving the chunk through
  build/review/deliver flips it to ``blizzard:in-progress`` at some point before it
  reaches ``done``, where both clear.
* **A stopped chunk clears** — a separate chunk stopped mid-flight (never claimed) has
  its marker cleared on the next sweep.
* **A hand-deleted label is re-asserted** — the hub holds no annotation state of its
  own, so a label removed out of band comes back on the next sweep rather than staying
  gone.
* **A down forge degrades to a skip, then re-converges** — the forge's own
  ``unreachable`` lever stands in for an outage: the hub keeps serving, the sweep logs a
  skip, and the label lands once the lever clears.

Skipped unless ``BLIZZARD_E2E=1`` with the sibling ``blizzard-mock`` worktree
provisioned — exactly like the other acceptance-loop scenarios.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from blizzard.runner.loop.build import run_single_tick
from tests.e2e.test_acceptance_loop import (
    FIXTURE_ENV,
    REPO,
    REPO_NAME,
    _forge,
    _free_port,
    _graph_yaml,
    _hub,
    _mock_bin_dir,
    _runner_api,
    _runner_config,
    _winter_source,
)
from tests.support import read_daemon_log

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e forge-status needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]


def _poll_until(predicate, *, timeout: float = 20.0, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _labels(forge: httpx.Client, number: int) -> set[str]:
    resp = forge.get(f"/repos/{REPO}/issues/{number}")
    assert resp.status_code == 200, resp.text
    return {label["name"] for label in resp.json()["labels"]}


def _ingest_and_promote(hub: httpx.Client, forge: httpx.Client, title: str) -> tuple[str, int]:
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "the chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id, number


def _drive_recording_labels(
    config, hub: httpx.Client, forge: httpx.Client, chunk_id: str, number: int, fenced_env: dict[str, str]
) -> tuple[str, list[set[str]]]:
    """:func:`~tests.e2e.test_acceptance_loop._drive_until_done`'s loop, plus a label
    snapshot per tick — the projection updates asynchronously (the hub's own background
    sweep, on its own 1s interval), so recording history across the whole drive is more
    robust than trying to catch ``blizzard:in-progress`` at one precisely-timed instant.
    """
    prior = dict(os.environ)
    os.environ.update(fenced_env)
    history: list[set[str]] = []
    try:
        with _runner_api(config):
            deadline = time.monotonic() + 120.0
            status = "ready"
            while time.monotonic() < deadline:
                run_single_tick(config)
                detail = hub.get(f"/api/chunks/{chunk_id}")
                assert detail.status_code == 200, detail.text
                status = detail.json()["status"]
                history.append(_labels(forge, number))
                if status in {"done", "stopped", "needs_human"}:
                    return status, history
                time.sleep(0.5)
            return status, history
    finally:
        os.environ.clear()
        os.environ.update(prior)


def test_forge_status_projection_e2e(tmp_path: Path) -> None:
    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    winter_source = _winter_source()
    if winter_source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    scratch = tmp_path / "scratch"
    subprocess.run(
        [
            str(bin_dir / "blizzard-mock-fixture"),
            "reset",
            "--env",
            FIXTURE_ENV,
            "--scratch-root",
            str(scratch),
            "--winter-source",
            str(winter_source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    (workspace / ".blizzard-mock-harness-fence").write_text("e2e fence marker\n")

    hub_dir = tmp_path / "hub"
    forge_port, hub_port = _free_port(), _free_port()
    with (
        _forge(bin_dir, origins, forge_port) as forge,
        _hub(hub_dir, forge_port, hub_port, annotate=True, annotation_interval_seconds=1) as hub,
    ):
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201

        # -- the happy path: ingested -> in-progress (at some point) -> cleared on done --
        chunk_a, number_a = _ingest_and_promote(hub, forge, "sweep me end to end")
        assert _poll_until(lambda: _labels(forge, number_a) == {"blizzard:ingested"}, timeout=15.0), _labels(
            forge, number_a
        )

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, max_agents=1)
        fenced = {**os.environ, "BLIZZARD_MOCK_HARNESS_FENCE": "1"}
        status, history = _drive_recording_labels(config, hub, forge, chunk_a, number_a, fenced)

        assert status == "done", f"chunk did not reach done (last status {status!r})"
        assert any("blizzard:in-progress" in labels for labels in history), (
            f"blizzard:in-progress never observed across the drive: {history}"
        )
        assert _poll_until(lambda: _labels(forge, number_a) == set(), timeout=15.0), _labels(forge, number_a)

        # -- a separate chunk, stopped mid-flight (never claimed), clears its marker --
        chunk_b, number_b = _ingest_and_promote(hub, forge, "stop me before a runner ever claims me")
        assert _poll_until(lambda: _labels(forge, number_b) == {"blizzard:ingested"}, timeout=15.0), _labels(
            forge, number_b
        )
        assert hub.post(f"/api/chunks/{chunk_b}/stop", json={"by": "e2e-test"}).status_code == 202
        assert _poll_until(lambda: _labels(forge, number_b) == set(), timeout=15.0), _labels(forge, number_b)

        # -- a label deleted by hand is re-asserted, since the hub holds no annotation
        #    state of its own and every sweep re-derives desired state from scratch --
        _chunk_c, number_c = _ingest_and_promote(hub, forge, "re-assert my hand-deleted label")
        assert _poll_until(lambda: _labels(forge, number_c) == {"blizzard:ingested"}, timeout=15.0), _labels(
            forge, number_c
        )
        deleted = forge.delete(f"/repos/{REPO}/issues/{number_c}/labels/blizzard:ingested")
        assert deleted.status_code == 200, deleted.text
        assert _labels(forge, number_c) == set()
        assert _poll_until(lambda: _labels(forge, number_c) == {"blizzard:ingested"}, timeout=15.0), (
            "the sweep did not re-assert the hand-deleted label"
        )

        # -- the forge stopped mid-run: the hub keeps serving, the sweep logs a skip
        #    (not a crash), and the projection re-converges once the forge returns.
        #    The `unreachable` lever stands in for a real process kill: killing the
        #    forge subprocess would also wipe its in-memory issue/label state, which
        #    would make "re-converges once the forge returns" untestable (there would
        #    be nothing left to converge back onto). --
        chunk_d, number_d = _ingest_and_promote(hub, forge, "outlive a forge outage")
        assert _poll_until(lambda: _labels(forge, number_d) == {"blizzard:ingested"}, timeout=15.0), _labels(
            forge, number_d
        )

        assert forge.post("/_levers/unreachable", json={}).status_code == 200
        try:
            time.sleep(3)  # a few sweep intervals' worth of real time, forge down throughout
            # The hub itself is unaffected — chunk transitions and reads are never
            # blocked on the forge being reachable.
            assert hub.get("/api/health").status_code == 200
            assert hub.get(f"/api/chunks/{chunk_d}").status_code == 200
        finally:
            assert forge.delete("/_levers/unreachable").status_code == 200

        assert _poll_until(lambda: _labels(forge, number_d) == {"blizzard:ingested"}, timeout=15.0), (
            "the projection did not re-converge once the forge returned"
        )

    log_text = read_daemon_log(hub_dir / "daemon.log")
    skip_lines = [line for line in log_text.splitlines() if "sources_skipped" in line and REPO_NAME in line]
    assert skip_lines, "the sweep should have logged a skip naming the source while the forge was unreachable"

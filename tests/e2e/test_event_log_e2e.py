"""Operational event log — end to end (issue #125, Phase 5, e2e tier).

The in-process scenario (modelled on ``test_escalation_e2e``): a real mock worker driven
to a **verdict-less** exit exhausts its retry budget, so the runner escalates — and its
Phase-3 emission surfaces a **critical** ``worker-lost`` operational event that (a) reads
back off the live ``GET /api/events`` and (b) arrives on the SSE spine as an
``event-logged`` frame. Every seam real (mock forge + hub + runner over a minted fixture),
no tokens, no network. Skipped unless ``BLIZZARD_E2E=1``.

The browser half — the Events tab rendering severity-then-recency, its filters, a live
event over SSE with no reload, and a row's chunk deep-link — lives in
``test_board_browser_e2e``'s companion scenario over the built bundle.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import httpx
import pytest

from blizzard.hub.events.broker import EVENT_LOGGED
from tests.e2e.test_acceptance_loop import (
    FIXTURE_ENV,
    REPO,
    REPO_NAME,
    _drive_until_done,
    _forge,
    _free_port,
    _hub,
    _mock_bin_dir,
    _runner_config,
    _winter_source,
)

# The built Angular bundle `blizzard hub host` mounts at `/`; the browser scenario needs it
# (like every other browser e2e — `mise run e2e` `depends = ["web-build"]`). Absent it, the
# board renders no app, so the scenario skips rather than fails against a placeholder.
_HUB_BUNDLE = Path(__file__).resolve().parents[2] / "src" / "blizzard" / "static" / "hub" / "index.html"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e event log needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# A build turn that does nothing of note, whose judgement emits NO verdict() — the adapter
# parses no <Choice>, the core fails the attempt; every attempt fails this way so the retry
# budget exhausts and the runner escalates (the worker-lost path).
_VERDICTLESS = "pass\n"


def _graph_yaml() -> str:
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _VERDICTLESS,
                "judgement": {
                    "prompt": _VERDICTLESS,
                    "choices": {"pass": {"description": "Green.", "to": "deliver"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "true"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Landed.", "to": "done"},
                        "conflict": {"description": "Conflict.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _sse_event_types(hub: httpx.Client) -> list[str]:
    """The event types on the live SSE spine's replay tail (``Last-Event-ID: 0`` replays
    the buffered frames the instant the stream opens).

    The tail flushes immediately on connect, then the live connection blocks awaiting the
    next event/keepalive — so a short read window captures the whole tail and the ensuing
    ``ReadTimeout`` is the expected end-of-tail signal, not a fault."""
    types: list[str] = []
    try:
        with hub.stream("GET", "/api/events/stream", headers={"Last-Event-ID": "0"}, timeout=3.0) as resp:
            assert resp.status_code == 200, resp.text
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    types.append(line.split(":", 1)[1].strip())
    except httpx.ReadTimeout:
        pass  # the buffered tail drained; the live stream then blocks — expected
    return types


def test_a_verdict_less_exit_surfaces_a_critical_worker_lost_event(tmp_path: Path) -> None:
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

    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "event-log", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced: dict[str, str] = {}
        status = _drive_until_done(config, hub, chunk_id, fenced)
        assert status == "needs_human", f"chunk did not derive needs_human (last status {status!r})"

        # (a) The critical worker-lost event reads back off the live GET /api/events.
        feed = hub.get("/api/events").json()["events"]
        lost = [e for e in feed if e["kind"] == "worker-lost" and e["chunk_id"] == chunk_id]
        assert lost, f"no worker-lost event for {chunk_id} in {feed}"
        assert lost[0]["severity"] == "critical"
        assert lost[0]["detail"]["via"] in {"advance", "reap"}

        # (b) ...and it fanned out on the SSE spine as an event-logged frame.
        assert EVENT_LOGGED in _sse_event_types(hub)


def _reset_fixture(bin_dir: Path, winter_source: Path, scratch: Path) -> tuple[Path, Path]:
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
    (workspace / ".blizzard-mock-harness-fence").write_text("e2e fence marker\n")
    return workspace, fixture_root / "origins"


def _push_event(
    hub: httpx.Client, *, seq: int, severity: str, kind: str, chunk_id: str, message: str, runner_id: str = "runner-ui"
) -> None:
    """Push one operational event straight through the hub's fold — the deterministic
    stand-in for the runner's own emission, so the browser sees a known feed."""
    payload = {"severity": severity, "kind": kind, "message": message, "chunk_id": chunk_id}
    resp = hub.post(
        "/api/fleet/events",
        json={"runner_id": runner_id, "facts": [{"seq": seq, "kind": "event.recorded", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text


def test_the_events_tab_renders_filters_and_updates_live_in_the_browser(
    tmp_path: Path, chromium_available: bool
) -> None:
    """The board's Events tab over the **built** bundle in a real browser: rows render
    severity-then-recency, a severity filter narrows them, a fresh event arrives **live over
    SSE with no reload**, and a row deep-links to its chunk (AC#6). Release-only tier — skips
    cleanly without Chromium or a built bundle, runs in the tag `release` full e2e tier."""
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    if not _HUB_BUNDLE.is_file():
        pytest.skip("no built hub bundle (run the web build — release tier drives `mise run e2e`)")
    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    winter_source = _winter_source()
    if winter_source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    from playwright.sync_api import expect, sync_playwright

    _workspace, origins = _reset_fixture(bin_dir, winter_source, tmp_path / "scratch")
    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        # A chunk the events name (the event_log.chunk_id FK) — its own state is immaterial.
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "ui events", "body": "the chunk"})
        chunk_id = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]}).json()[
            "chunk_id"
        ]
        # Seed a mixed-severity feed (recency ascending; severity mixed) so the render order
        # proves the severity-then-recency sort, not mere arrival order.
        _push_event(hub, seq=1, severity="info", kind="attempt-abandoned", chunk_id=chunk_id, message="abandoned")
        _push_event(hub, seq=2, severity="warning", kind="attempt-failed", chunk_id=chunk_id, message="retried")
        _push_event(hub, seq=3, severity="critical", kind="worker-lost", chunk_id=chunk_id, message="lost")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(f"http://127.0.0.1:{hub_port}/", wait_until="load")
                expect(page.get_by_test_id("board-shell")).to_be_visible()

                # Open the Events tab from the desktop nav.
                page.get_by_test_id("nav-events").click()
                expect(page.get_by_test_id("events-panel")).to_be_visible()
                expect(page.get_by_test_id("events-row")).to_have_count(3)
                # Severity-then-recency: the critical row is first, even though it arrived last.
                expect(page.get_by_test_id("events-severity").first).to_have_text("critical")

                # The severity filter narrows the list, then restores it.
                page.get_by_test_id("events-filter-critical").click()
                expect(page.get_by_test_id("events-row")).to_have_count(1)
                page.get_by_test_id("events-filter-all").click()
                expect(page.get_by_test_id("events-row")).to_have_count(3)

                # A fresh event arrives LIVE over SSE — no reload — and slots in at the top.
                _push_event(hub, seq=4, severity="critical", kind="worker-lost", chunk_id=chunk_id, message="fresh")
                expect(page.get_by_test_id("events-row")).to_have_count(4)

                # A row deep-links to its chunk (the board).
                page.get_by_test_id("events-chunk").first.click()
                expect(page.get_by_test_id("board-shell")).to_be_visible()
            finally:
                browser.close()

"""Operational event log — end to end (issue #125, Phase 5, e2e tier).

A verdict-less exit exhausts the retry budget and escalates; a critical `worker-lost`
event must read back off `GET /api/events` and arrive on the SSE spine. Skipped unless
``BLIZZARD_E2E=1``. Browser coverage lives in the tests below (issue #213 for the rail).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from playwright.sync_api import ViewportSize

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

# The built Angular bundle `blizzard hub host` mounts at `/`, needed for the browser
# scenarios; absent it, they skip rather than fail against a placeholder.
_HUB_BUNDLE = Path(__file__).resolve().parents[2] / "src" / "blizzard" / "static" / "hub" / "index.html"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e event log needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# A build turn that does nothing of note, whose judgement emits NO verdict() — every
# attempt fails, so the retry budget exhausts and the runner escalates.
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
    """The event types on the SSE spine's replay tail (``Last-Event-ID: 0`` replays the
    buffered frames on connect). The tail flushes immediately, then the live connection
    blocks — so a short read window captures it, and the ensuing ``ReadTimeout`` is
    expected, not a fault."""
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
    """The board's Events tab over the built bundle: rows render severity-then-recency, a
    severity filter narrows them, a fresh event arrives live over SSE, and a row deep-links
    to its chunk (AC#6)."""
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

                # One runner in the feed so far — nothing to narrow by, so the runner filter
                # row is hidden (AC#6: the runner axis exists, but only when it discriminates).
                expect(page.get_by_test_id("events-runner-filter")).to_have_count(0)

                # A fresh event from a SECOND runner arrives LIVE over SSE — no reload — so the
                # feed grows and the runner filter row now appears (two runners to choose from).
                _push_event(
                    hub,
                    seq=1,
                    severity="critical",
                    kind="worker-lost",
                    chunk_id=chunk_id,
                    message="other runner",
                    runner_id="runner-two",
                )
                expect(page.get_by_test_id("events-row")).to_have_count(4)
                expect(page.get_by_test_id("events-runner-filter")).to_be_visible()

                # The runner filter narrows to just the second runner's event, then restores.
                page.get_by_test_id("events-runner-filter-runner-two").click()
                expect(page.get_by_test_id("events-row")).to_have_count(1)
                expect(page.get_by_test_id("events-message").first).to_have_text("other runner")
                page.get_by_test_id("events-runner-filter-all").click()
                expect(page.get_by_test_id("events-row")).to_have_count(4)

                # A row deep-links to its chunk (the board).
                page.get_by_test_id("events-chunk").first.click()
                expect(page.get_by_test_id("board-shell")).to_be_visible()
            finally:
                browser.close()


def test_the_events_grid_does_not_collapse_at_a_narrow_viewport(
    tmp_path: Path, chromium_available: bool, narrow_viewport: ViewportSize
) -> None:
    """Narrow-viewport fallback (issue #155) for the Events tab's time-first grid (issue
    #153/#154): at ~390px width a long-message row stays a bounded height and the page
    gains no horizontal scroll (issue #171's narrow-viewport tier rule)."""
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
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "narrow events", "body": "the chunk"})
        chunk_id = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]}).json()[
            "chunk_id"
        ]
        _push_event(
            hub,
            seq=1,
            severity="warning",
            kind="attempt-failed",
            chunk_id=chunk_id,
            message="retry budget exhausted after the third consecutive verdict-less attempt in a row",
        )

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport=narrow_viewport)
            try:
                page.goto(f"http://127.0.0.1:{hub_port}/events", wait_until="load")
                expect(page.get_by_test_id("events-row")).to_have_count(1)

                box = page.get_by_test_id("events-row").first.bounding_box()
                assert box is not None, "the events row has no layout box"
                # A generous ceiling catches the collapse-class defect without pinning
                # an exact pixel height (broken: ~848px; fixed: ~74px).
                assert box["height"] < 200, f"row height {box['height']}px — the grid collapsed at a narrow width"

                no_overflow = page.evaluate(
                    "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
                )
                assert no_overflow, "the page gained horizontal scroll at a narrow viewport"
            finally:
                browser.close()


def test_the_rail_survives_a_reload_with_no_duplicate_or_missing_rows(tmp_path: Path, chromium_available: bool) -> None:
    """Event log rail backfill (issue #213): after a hub restart (fresh replay ring, same
    on-disk store), a reload shows the same row count and chunk-ref set as before restart —
    proving `GET /api/activity` backfill, not leftover live replay."""
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
    hub_dir = tmp_path / "hub"  # reused verbatim across the restart — the durable store lives here

    with _forge(bin_dir, origins, forge_port) as forge, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            with _hub(hub_dir, forge_port, hub_port) as hub:
                issue_a = forge.post(f"/repos/{REPO}/issues", json={"title": "reload seam A", "body": "chunk a"})
                chunk_a = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue_a.json()['number']}"]}).json()[
                    "chunk_id"
                ]
                issue_b = forge.post(f"/repos/{REPO}/issues", json={"title": "reload seam B", "body": "chunk b"})
                _chunk_b = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue_b.json()['number']}"]}).json()[
                    "chunk_id"
                ]  # its own mint row is asserted on generically below, by count/shape
                _push_event(
                    hub,
                    seq=1,
                    severity="warning",
                    kind="reload-seam-probe",
                    chunk_id=chunk_a,
                    message="a probed operational event",
                    runner_id="runner-reload-seam",
                )

                page.goto(f"http://127.0.0.1:{hub_port}/", wait_until="load")
                expect(page.get_by_test_id("board-shell")).to_be_visible()
                expect(page.get_by_test_id("event-log-panel")).to_be_visible()
                # Facts already landed before this subscribes, so the replay tail alone
                # carries them — this is the baseline row set, not yet the restart assertion.
                expect(page.get_by_test_id("event-log-row")).to_have_count(3)
                first_load_messages = page.get_by_test_id("event-log-message").all_text_contents()

            # The hub exits here; facts are durable (sqlite) but `EventBroker`'s replay
            # ring is not — the next `_hub()` call starts a fresh, empty one over the same store.
            with _hub(hub_dir, forge_port, hub_port):
                page.reload(wait_until="load")
                expect(page.get_by_test_id("event-log-panel")).to_be_visible()
                # The fresh broker's replay tail is empty — only `GET /api/activity`
                # backfill can repopulate these rows, at the same count as the baseline.
                expect(page.get_by_test_id("event-log-row")).to_have_count(3)
                reload_messages = page.get_by_test_id("event-log-message").all_text_contents()

                # The event-logged row carries the same severity/kind fields whichever
                # source renders it, so its text matches exactly across both loads.
                event_message = next(m for m in first_load_messages if "reload-seam-probe" in m)
                reload_event_message = next(m for m in reload_messages if "reload-seam-probe" in m)
                assert reload_event_message == event_message, (reload_messages, first_load_messages)

                # Live-rendered rows carry the `→` transition arrow; backfill-rendered rows
                # are the bare chunk ref — the flip proves the backfill path, not leftover replay.
                first_mint_rows = [m for m in first_load_messages if m != event_message]
                reload_mint_rows = [m for m in reload_messages if m != reload_event_message]
                assert len(first_mint_rows) == len(reload_mint_rows) == 2, (first_mint_rows, reload_mint_rows)
                assert all("→" in m for m in first_mint_rows), first_mint_rows
                assert all("→" not in m for m in reload_mint_rows), reload_mint_rows
                first_refs = {m.split(" ")[0] for m in first_mint_rows}
                reload_refs = set(reload_mint_rows)
                assert len(first_refs) == len(reload_refs) == 2, (first_refs, reload_refs)
                assert first_refs == reload_refs, (first_refs, reload_refs)
        finally:
            browser.close()

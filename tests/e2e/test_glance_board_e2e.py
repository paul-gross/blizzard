"""Mobile glance board — narrow-viewport browser e2e (blizzard#181, Phase 5).

The glance board (`hub/board/glance/glance-board.ts` + `glance-view.ts`) is the mobile
shell's `/board` route (`app.routes.ts`'s `matchesMobileViewport`-guarded entry) — this
proves the loading-vs-empty distinction (AC 4) holds there too, at a real ~390px phone
width (`bzh:narrow-viewport-tier-rule`): while the chunks read is still in flight, the
"Needs you" panel shows loading copy, never its empty copy; once the read resolves on a
populated fleet, the loading copy is gone and the row is there instead of the empty copy
having ever taken its place. Every seam real (mock forge + hub + runner over a minted
`blizzard-mock` fixture), no tokens, no network. Skipped unless `BLIZZARD_E2E=1`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Route, ViewportSize

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

_HUB_BUNDLE = Path(__file__).resolve().parents[2] / "src" / "blizzard" / "static" / "hub" / "index.html"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e glance board needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# A build turn with no <Choice> in its judgement — every attempt fails the same way, so
# the retry budget exhausts and the chunk escalates to needs_human (same shape as
# test_event_log_e2e.py's own verdict-less graph), landing it in "Needs you".
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


def test_the_glance_board_shows_loading_before_rows_and_never_empty_on_a_populated_fleet(
    tmp_path: Path, chromium_available: bool, narrow_viewport: ViewportSize
) -> None:
    """At a real ~390px phone width, `/board` routes to the glance shell (the desktop
    `BoardPage` never matches there). With a needs_human chunk already on the fleet, a
    held-open `GET /api/chunks` response keeps the "Needs you" panel in its loading state
    for an observable window — proving the loading copy renders instead of the empty
    copy — before the read is released to resolve to the populated row.

    Fails against a `board-shell.ts`-style bare `@if (length === 0)` check (the defect
    this plan fixes); passes once `KitAsyncState` gates the empty copy on the query's own
    settled state. Release-only tier — skips cleanly without Chromium or a built bundle.
    """
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
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "glance board", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced: dict[str, str] = {}
        status = _drive_until_done(config, hub, chunk_id, fenced)
        assert status == "needs_human", f"chunk did not derive needs_human (last status {status!r})"

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport=narrow_viewport)
            try:
                # Hold the chunks read open indefinitely rather than racing a fixed delay
                # against a same-machine round trip: the handler just captures the route
                # and returns without resolving it, so the request stays genuinely pending
                # — deterministically, no timing guess — until this test releases it below.
                # (A handler that itself blocks with time.sleep() before calling
                # route.continue_() would block Playwright's own single-threaded sync
                # dispatcher, wedging every other page call for the same duration.)
                held: list[Route] = []
                page.route("**/api/chunks", lambda route: held.append(route))

                # `commit`, not `load`: `load` does not fire until every request already
                # in flight settles, which would wait on the very request this test is
                # holding open — this needs the DOM queryable before that response arrives.
                page.goto(f"http://127.0.0.1:{hub_port}/board", wait_until="commit")

                # The mobile shell, not the desktop board — the guard matched.
                expect(page.get_by_test_id("glance-board")).to_be_visible()
                expect(page.get_by_test_id("needs-you-loading")).to_be_visible()
                expect(page.get_by_test_id("needs-you-empty")).to_have_count(0)

                # Release the held read: the row lands, and the empty copy is never what
                # took its place in between.
                for route in held:
                    route.continue_()
                expect(page.get_by_test_id("needs-you-row")).to_have_count(1)
                expect(page.get_by_test_id("needs-you-loading")).to_have_count(0)
                expect(page.get_by_test_id("needs-you-empty")).to_have_count(0)
            finally:
                browser.close()

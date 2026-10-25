"""Mobile glance board — narrow-viewport browser e2e.

At a real ~390px phone width (`bzh:narrow-viewport-tier-rule`), `/board` routes to the
glance shell. Asserts the loading-vs-empty distinction (AC 4): while the chunks read is
in flight the list shows loading, never empty. Skipped unless `BLIZZARD_E2E=1`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Route, ViewportSize

from tests.e2e.test_board_cost_live_e2e import _ingest_promote_claim, _push_usage
from tests.fleet_launchers import REPO, drive_until_done, mock_bin_dir, run_forge, run_hub, runner_config
from tests.fleet_world import FIXTURE_ENV, REPO_NAME, mint_world
from tests.support import free_port

_HUB_BUNDLE = Path(__file__).resolve().parents[2] / "src" / "blizzard" / "static" / "hub" / "index.html"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e glance board needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# A build turn with no <Choice> in its judgement, so every attempt fails the same way and
# the chunk escalates to needs_human — landing it in "Needs you".
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
                "action": "test.answer",
                "with": {"choice": "landed"},
                "judgement": {
                    "choices": {
                        "landed": {"description": "Landed.", "to": "done"},
                        "conflict": {"description": "Conflict.", "to": "build"},
                        "failure": {"description": "Failed.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def test_the_glance_board_shows_loading_before_rows_and_never_empty_on_a_populated_fleet(
    tmp_path: Path, chromium_available: bool, narrow_viewport: ViewportSize
) -> None:
    """A held-open `GET /api/chunks` keeps the list in loading state; asserted to
    show loading, never empty, then land the card once released.
    """
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    if not _HUB_BUNDLE.is_file():
        pytest.skip("no built hub bundle (run the web build — release tier drives `mise run e2e`)")
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")

    from playwright.sync_api import expect, sync_playwright

    scratch = tmp_path / "scratch"
    mint_world(bin_dir, scratch)
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "glance board", "body": "the chunk"})
        assert issue.status_code == 201, issue.text
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        status = drive_until_done(config, hub, chunk_id, fenced)
        assert status == "needs_human", f"chunk did not derive needs_human (last status {status!r})"

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport=narrow_viewport)
            try:
                # Hold the chunks read open by capturing the route rather than racing a
                # fixed delay or blocking with time.sleep() (would wedge Playwright's dispatcher).
                held: list[Route] = []
                page.route("**/api/chunks?**", lambda route: held.append(route))

                # `commit`, not `load`: `load` waits on every in-flight request, including
                # the one this test holds open.
                page.goto(f"http://127.0.0.1:{hub_port}/board", wait_until="commit")

                # The mobile shell, not the desktop board — the guard matched.
                expect(page.get_by_test_id("glance-board")).to_be_visible()
                expect(page.get_by_test_id("glance-list-loading")).to_be_visible()
                expect(page.get_by_test_id("glance-list-empty")).to_have_count(0)

                # Release the held read: the card lands, and the empty copy is never what
                # took its place in between.
                for route in held:
                    route.continue_()
                expect(page.locator(f'[data-testid="glance-card"][data-chunk="{chunk_id}"]')).to_have_count(1)
                expect(page.get_by_test_id("glance-list-loading")).to_have_count(0)
                expect(page.get_by_test_id("glance-list-empty")).to_have_count(0)
            finally:
                browser.close()


def test_the_glance_board_shows_a_cost_estimate_never_a_plain_billed_zero(
    tmp_path: Path, chromium_available: bool, narrow_viewport: ViewportSize
) -> None:
    """The glance spend panel and the running chunk's card render the combined figure, marked `~`,
    never a plain billed `$0.00`, for a chunk with no billed cost."""
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    if not _HUB_BUNDLE.is_file():
        pytest.skip("no built hub bundle (run the web build — release tier drives `mise run e2e`)")
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")

    from playwright.sync_api import expect, sync_playwright

    scratch = tmp_path / "scratch"
    mint_world(bin_dir, scratch)
    origins = scratch / FIXTURE_ENV / "origins"

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        chunk_id, node_id = _ingest_promote_claim(forge, hub, "chunk — glance cost estimate")

        _push_usage(
            hub,
            chunk_id=chunk_id,
            node_id=node_id,
            seq=1,
            cost_usd=None,
            input_tokens=900,
            output_tokens=400,
            estimated_cost_usd=0.07,
        )

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport=narrow_viewport)
            try:
                page.goto(f"http://127.0.0.1:{hub_port}/board", wait_until="load")
                expect(page.get_by_test_id("glance-board")).to_be_visible()

                # The spend panel: the combined figure renders as the estimate alone, marked
                # `~` — never a plain billed $0.00 in its place.
                expect(page.get_by_test_id("glance-spend-value")).to_have_text("~$0.07")

                # The running chunk's card: same rule, same figure.
                card = page.locator(f'[data-testid="glance-card"][data-chunk="{chunk_id}"]')
                expect(card).to_have_count(1)
                expect(card.get_by_test_id("glance-cost")).to_have_text("~$0.07")
            finally:
                browser.close()

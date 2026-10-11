"""Project lens browser e2e — the board's project lens against a served bundle and a real hub.

A real Chromium (Playwright) opens lensed board URLs: a deep link to ``/p/default/board`` opens the board under the
``default`` project's lens; a link naming a project by a slug it has left, or by its id, is rewritten in place to the
project's current slug; and a link naming no project of the tenant opens the same view on All, with a notice. The lens
guard and the board reads it narrows are unit-tested; this is the one place the URL rewrite runs in a real router
against the hub's own project resolution. No runner or forge traffic. Needs the built bundle (``mise run web-build``)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.e2e.test_acceptance_loop import _free_port, _hub

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e project lens needs the served bundle; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]


def test_project_lens_deep_links_resolve_in_the_browser(tmp_path: Path, chromium_available: bool) -> None:
    """A lensed deep link opens under its lens; a former slug and an id are rewritten; an unknown project opens All."""
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    from playwright.sync_api import expect, sync_playwright

    forge_port, hub_port = _free_port(), _free_port()
    with _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        created = hub.post("/api/projects", json={"slug": "glacier", "name": "Glacier", "description": ""})
        assert created.status_code == 201, created.text
        project = created.json()
        moved = hub.patch(
            f"/api/projects/{project['project_id']}",
            json={"slug": "ice-shelf"},
            headers={"If-Match": str(project["revision"])},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["slug"] == "ice-shelf"

        base = f"http://127.0.0.1:{hub_port}"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            expect.set_options(timeout=20_000)
            try:
                # --- A deep link under the default project's lens opens the board there ---------
                page.goto(f"{base}/p/default/board", wait_until="load")
                expect(page.get_by_test_id("board-shell")).to_be_visible()
                expect(page).to_have_url(f"{base}/p/default/board")
                expect(page.get_by_test_id("project-lens-default")).to_have_attribute("aria-pressed", "true")

                # --- A slug the project has left is replaced by its current slug ----------------
                page.goto(f"{base}/p/glacier/board", wait_until="load")
                expect(page).to_have_url(f"{base}/p/ice-shelf/board")
                expect(page.get_by_test_id("board-shell")).to_be_visible()
                expect(page.get_by_test_id("project-lens-ice-shelf")).to_have_attribute("aria-pressed", "true")

                # --- So is the project's id -------------------------------------------------------
                page.goto(f"{base}/p/{project['project_id']}/board", wait_until="load")
                expect(page).to_have_url(f"{base}/p/ice-shelf/board")

                # --- A project the tenant does not hold opens the same view on All, with a notice -
                page.goto(f"{base}/p/no-such-project/board", wait_until="load")
                expect(page).to_have_url(f"{base}/board")
                expect(page.get_by_test_id("lens-notice")).to_be_visible()
                expect(page.get_by_test_id("project-lens-all")).to_have_attribute("aria-pressed", "true")
            finally:
                browser.close()

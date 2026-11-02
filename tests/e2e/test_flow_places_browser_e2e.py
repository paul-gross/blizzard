"""Flow places browser e2e — the board's place lines over a real hub landing two chunks on one branch.

Held by the mock forge's ``checks_pending`` lever, both chunks stand in one flow's line; one board load, never
reloaded, shows the cards' places, then the places clearing live once the lever clears, and the flow's record then
holds both landings. Needs ``BLIZZARD_E2E=1``, ``blizzard-mock``, Chromium, and the built bundle
(``mise run web-build``)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests.e2e.flow_support import flow_runner_config, flow_world, pinned_chunk, set_lever, ticking_runner
from tests.fleet_launchers import REPO, run_forge, run_hub
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e flow places need the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_LANDING_TIMEOUT = 60_000


def test_the_board_shows_each_place_on_a_branch_and_clears_it_live_once_it_lands(
    tmp_path: Path, chromium_available: bool
) -> None:
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    from playwright.sync_api import expect, sync_playwright

    bin_dir, workspace, origins = flow_world(tmp_path)
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        set_lever(forge, REPO, armed=True)
        with ticking_runner(flow_runner_config(tmp_path, workspace, bin_dir, hub_port)) as tick_until:
            first = pinned_chunk(hub, forge, "FIRST.md")
            tick_until(lambda: hub.get(f"/api/chunks/{first}").json()["pending"] is not None)
            second = pinned_chunk(hub, forge, "SECOND.md")
            tick_until(lambda: hub.get(f"/api/chunks/{second}").json()["pending"] is not None)

        [flow] = hub.get("/api/flows", params={"active": "true"}).json()["flows"]
        assert [place["chunk_id"] for place in sorted(flow["line"], key=lambda p: p["arrival"])] == [first, second]
        path = f"{flow['repository_name'] or flow['repository_slug']}/{flow['branch']}"

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            expect.set_options(timeout=20_000)
            try:
                # --- One load, never reloaded: each delivering card says its place on the branch ---
                page.goto(f"http://127.0.0.1:{hub_port}/board", wait_until="load")
                place = re.escape(path)
                first_card = page.locator(f'[data-testid="chunk-card"][data-chunk="{first}"]')
                second_card = page.locator(f'[data-testid="chunk-card"][data-chunk="{second}"]')
                expect(first_card.get_by_test_id("card-flow-place")).to_have_text(
                    re.compile(rf"^\s*(landing on|1st on) {place}\s*$")
                )
                expect(second_card.get_by_test_id("card-flow-place")).to_have_text(
                    re.compile(rf"^\s*(landing on|1st on|2nd on) {place}\s*$")
                )

                # --- The checks go green: both land, and the places clear live ------------------
                set_lever(forge, REPO, armed=False)
                expect(page.get_by_test_id("card-flow-place")).to_have_count(0, timeout=_LANDING_TIMEOUT)
                for chunk_id in (first, second):
                    detail = hub.get(f"/api/chunks/{chunk_id}").json()
                    assert (detail["status"], detail["landed"]) == ("done", True), detail["history"]

                # --- The flow's record holds both landings --------------------------------------
                facts = hub.get(f"/api/flows/{flow['flow_id']}/record").json()["facts"]
                assert {fact["chunk_id"] for fact in facts if fact["kind"] == "landed"} == {first, second}
            finally:
                browser.close()

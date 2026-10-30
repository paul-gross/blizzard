"""Flow lanes browser e2e — the board's Delivery band and place lines over a real hub landing two chunks on one branch.

Held by the mock forge's ``checks_pending`` lever, both chunks stand in one flow's line; one board load, never
reloaded, shows the lane and the cards' places, then the lane collapsing live once the lever clears, then the record
in the dock. Needs ``BLIZZARD_E2E=1``, ``blizzard-mock``, Chromium, and the built bundle (``mise run web-build``)."""

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
        reason="e2e flow lanes need the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_LANDING_TIMEOUT = 60_000


def test_the_delivery_band_shows_a_branch_line_and_collapses_live_once_it_lands(
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
                # --- One load, never reloaded: the branch's lane and the line it holds -----------
                page.goto(f"http://127.0.0.1:{hub_port}/board", wait_until="load")
                lane = page.locator(f'[data-testid="delivery-lane"][data-flow="{flow["flow_id"]}"]')
                expect(page.get_by_test_id("delivery-lane")).to_have_count(1)
                expect(lane.get_by_test_id("delivery-lane-title")).to_contain_text(flow["branch"])
                standing = lane.locator('[data-testid="delivery-landing"], [data-testid="delivery-in-line"]')
                expect(standing).to_have_count(2)
                expect(standing.nth(0)).to_have_attribute("data-chunk", first)
                expect(standing.nth(1)).to_have_attribute("data-chunk", second)
                assert lane.get_by_test_id("delivery-landing").count() <= 1
                expect(lane).not_to_have_class(re.compile(r"\bcollapsed\b"))

                # --- Each delivering card says its place on the branch ----------------------------
                place = re.escape(path)
                first_card = page.locator(f'[data-testid="chunk-card"][data-chunk="{first}"]')
                second_card = page.locator(f'[data-testid="chunk-card"][data-chunk="{second}"]')
                expect(first_card.get_by_test_id("card-flow-place")).to_have_text(
                    re.compile(rf"^\s*(landing on|1st on) {place}\s*$")
                )
                expect(second_card.get_by_test_id("card-flow-place")).to_have_text(
                    re.compile(rf"^\s*(landing on|1st on|2nd on) {place}\s*$")
                )

                # --- The checks go green: both land, and the lane collapses live ------------------
                set_lever(forge, REPO, armed=False)
                expect(lane).to_have_class(re.compile(r"\bcollapsed\b"), timeout=_LANDING_TIMEOUT)
                expect(standing).to_have_count(0)
                expect(lane.get_by_test_id("delivery-verdict")).to_contain_text("landed")
                expect(page.get_by_test_id("card-flow-place")).to_have_count(0)
                for chunk_id in (first, second):
                    detail = hub.get(f"/api/chunks/{chunk_id}").json()
                    assert (detail["status"], detail["landed"]) == ("done", True), detail["history"]

                # --- The lane's title opens the flow's record in the dock -------------------------
                lane.get_by_test_id("delivery-lane-title").click()
                expect(page).to_have_url(re.compile(rf"[?&]flow={re.escape(flow['flow_id'])}(&|$)"))
                record = page.get_by_test_id("flow-record")
                expect(record).to_be_visible()
                landings = record.locator('[data-testid="flow-record-fact"][data-kind="landed"]')
                expect(landings).to_have_count(2)
                landed = {
                    landings.nth(n).get_by_test_id("flow-record-chunk").get_attribute("data-chunk") for n in (0, 1)
                }
                assert landed == {first, second}
            finally:
                browser.close()

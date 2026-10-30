"""Board cost/usage live-over-SSE e2e — the `test_board_cost_live_e2e` scenario of the standing e2e smoke.

A real Chromium (Playwright) over the served board wired to a live hub, asserting
cost/usage figures render end to end and update live with no reload when a
``usage.recorded`` fact lands. No runner is driven; skipped unless ``BLIZZARD_E2E=1``
and Chromium is installed (``uv run playwright install chromium``)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests.e2e.support import _ingest_promote_claim, _push_usage
from tests.fleet_launchers import mock_bin_dir, run_forge, run_hub
from tests.fleet_world import FIXTURE_ENV, graph_yaml, mint_world
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e board cost/usage needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]


def test_board_renders_cost_and_updates_live_over_sse(tmp_path: Path, chromium_available: bool) -> None:
    """Cost/usage renders on the board + chunk detail and updates live over SSE."""
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    from playwright.sync_api import expect, sync_playwright

    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")

    # 1. Mint a fresh, disposable fixture world (bare origins + a forge over them).
    scratch = tmp_path / "scratch"
    mint_world(bin_dir, scratch)
    origins = scratch / FIXTURE_ENV / "origins"

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": graph_yaml()}).status_code == 201
        chunk_id, node_id = _ingest_promote_claim(forge, hub, "chunk — cost render")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            expect.set_options(timeout=20_000)
            try:
                # --- Load the board ONCE. It is never reloaded again. -------------------
                page.goto(f"http://127.0.0.1:{hub_port}/", wait_until="load")
                expect(page.get_by_test_id("board-shell")).to_be_visible()

                running_col = page.locator('[data-col="running"]')
                card = running_col.get_by_test_id("chunk-card")
                expect(card).to_have_count(1)  # the claimed chunk derives running

                # Before any usage: spend reads zero (not partial) and the card carries no
                # cost figure — the baseline the live SSE update below must move off.
                expect(page.get_by_test_id("spend-today-value")).to_have_text("$0.00")
                expect(card.get_by_test_id("card-cost")).to_have_count(0)

                # A usage fact lands at the hub; the board re-derives with no reload
                # (Playwright's assertions poll until the live update lands).
                _push_usage(
                    hub,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    seq=1,
                    cost_usd=0.42,
                    input_tokens=1200,
                    output_tokens=800,
                    cache_read_tokens=300,
                    cache_create_tokens=100,
                )

                # Card figure and header spend-today both appear live off one fact, unmarked.
                expect(card.get_by_test_id("card-cost")).to_have_text("$0.42")
                expect(page.get_by_test_id("spend-today-value")).to_have_text("$0.42")

                # --- Detail dock: header total + the token line --------------------------
                card.click()
                expect(page.get_by_test_id("chunk-detail")).to_be_visible()
                expect(page.get_by_test_id("cost-total-usd")).to_contain_text("$0.42")
                expect(page.get_by_test_id("cost-total-usd")).not_to_have_attribute("title", re.compile(".+"))

                # All four token classes render inline, one labelled row apiece, no expand toggle.
                expect(page.get_by_test_id("fact-tokens-input")).to_have_text("1.2k")
                expect(page.get_by_test_id("fact-tokens-output")).to_have_text("800")
                expect(page.get_by_test_id("fact-tokens-cache-read")).to_have_text("300")
                expect(page.get_by_test_id("fact-tokens-cache-creation")).to_have_text("100")

                # A cost-absent (crash/reap-path) fact makes the total a lower bound; the
                # partial marker must appear live everywhere it renders.
                _push_usage(hub, chunk_id=chunk_id, node_id=node_id, seq=2, cost_usd=None)

                # Marked live, no reload: the detail total, the card figure, and the header
                # spend-today figure all gain the trailing `+` lower-bound mark.
                expect(page.get_by_test_id("cost-total-usd")).to_have_attribute("title", re.compile("lower bound"))
                expect(page.get_by_test_id("cost-total-usd")).to_contain_text("$0.42+")
                expect(card.get_by_test_id("card-cost")).to_have_text("$0.42+")
                expect(page.get_by_test_id("spend-today-value")).to_have_text("$0.42+")

                # --- A second, subscription-only chunk: an estimate, no billed cost -------
                estimate_chunk_id, estimate_node_id = _ingest_promote_claim(forge, hub, "chunk — cost estimate render")
                estimate_card = page.locator(f'[data-chunk="{estimate_chunk_id}"]')
                expect(estimate_card).to_have_count(1)
                expect(estimate_card.get_by_test_id("card-cost")).to_have_count(0)

                # `seq` continues runner r1's own stream: the hub dedupes store-and-forward
                # facts per (runner, seq), so reusing an earlier seq is silently dropped.
                _push_usage(
                    hub,
                    chunk_id=estimate_chunk_id,
                    node_id=estimate_node_id,
                    seq=3,
                    cost_usd=None,
                    input_tokens=900,
                    output_tokens=400,
                    estimated_cost_usd=0.07,
                )

                # The card shows the combined figure live — entirely estimated, so it reads
                # as the estimate alone, marked `~`, and never a separate billed figure.
                expect(estimate_card.get_by_test_id("card-cost")).to_have_text("~$0.07")

                # The header's spend-today figure is fleet-wide: the first chunk's $0.42
                # billed-and-PARTIAL plus this chunk's $0.07 estimate, both markers on one figure.
                expect(page.get_by_test_id("spend-today-value")).to_have_text("~$0.49+")

                # The detail dock: this chunk's own figure, marked `~` (entirely estimated) and
                # never `+` — an estimate-only row does not make a chunk PARTIAL.
                estimate_card.click()
                expect(page.get_by_test_id("cost-total-usd")).to_have_text("~$0.07")
                expect(page.get_by_test_id("cost-total-usd")).not_to_have_attribute("title", re.compile(".+"))
            finally:
                browser.close()

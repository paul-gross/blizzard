"""The gardening run dialog, in a real browser — the `test_gardening_run_dialog_browser_e2e`
scenario of the standing e2e smoke (blizzard#392 D6).

A real Chromium (Playwright) over the served board opens the dialog from the routines
list, proving the create-then-run submission (D3) and the never-swept delta-steering
(D5) round-trip against a live hub — no unit test reaches the real dialog chrome
(`KitDialog`'s own focus trap) or a real `POST`/`POST` pair landing a chunk. No runner
or forge traffic: the routine's run mints a queued chunk, never executed here. Needs
the built bundle (``mise run web-build``)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from tests.e2e.test_acceptance_loop import _free_port, _hub

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e gardening run dialog needs the served bundle; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]


def _graph_yaml(name: str) -> str:
    """The smallest valid graph — one hub-executed node, no prompt needed — enough for
    a routine to name, never reached: this scenario never drives a runner."""
    graph = {
        "name": name,
        "entry": "work",
        "nodes": {
            "work": {
                "executor": "hub",
                "run": [{"command": "true"}],
                "judgement": {"choices": {"landed": {"description": "Done.", "to": "done"}}},
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def test_gardening_run_dialog_browser(tmp_path: Path, chromium_available: bool) -> None:
    """Opens the run dialog from the routines list (the blizzard#392 D7 provisional
    trigger), mints a new scope before running (D3), and lands on the confirmation
    naming a real chunk id and linking to the board — against a live hub, no fixtures."""
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    from playwright.sync_api import expect, sync_playwright

    forge_port, hub_port = _free_port(), _free_port()
    # No forge is started: minting a queued run needs no work-source traffic, and this
    # scenario never drives a runner to execute it.
    with _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        graph_created = hub.post("/api/graphs", json={"definition_yaml": _graph_yaml("gardening-e2e-graph")})
        assert graph_created.status_code == 201, graph_created.text

        routine_created = hub.post(
            "/api/routines",
            json={
                "name": "gardening-e2e-routine",
                "graph_name": "gardening-e2e-graph",
                "default_scope_slug": "gardening-e2e-default",
                "default_model": [],
                "default_effort": None,
            },
        )
        assert routine_created.status_code == 201, routine_created.text
        routine_id = routine_created.json()["routine_id"]

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            expect.set_options(timeout=20_000)
            try:
                # --- The routines list is the D7 provisional trigger ---------------------
                page.goto(f"http://127.0.0.1:{hub_port}/gardening/routines", wait_until="load")
                row = page.get_by_test_id("gardening-routine-row")
                expect(row).to_be_visible()
                expect(row.get_by_test_id("gardening-routine-name")).to_have_text("gardening-e2e-routine")

                page.get_by_test_id(f"gardening-routine-run-{routine_id}").click()
                dialog = page.get_by_test_id("gardening-run-dialog")
                expect(dialog).to_be_visible()
                expect(page.get_by_test_id("run-dialog-title")).to_contain_text("gardening-e2e-routine")

                # --- The never-swept default scope steers to full (D5) -------------------
                expect(page.get_by_test_id("run-scope-option-gardening-e2e-default")).to_be_checked()
                expect(page.get_by_test_id("run-mode-never-swept")).to_be_visible()
                expect(page.get_by_test_id("run-mode-delta")).to_be_disabled()

                # --- D3: mint a new scope, with its description, before the run ----------
                page.get_by_test_id("run-scope-option-new").click()
                submit = page.get_by_test_id("run-dialog-submit")
                expect(submit).to_be_disabled()

                page.get_by_test_id("run-new-scope-slug").fill("gardening-e2e-fresh")
                expect(submit).to_be_disabled()
                page.get_by_test_id("run-new-scope-description").fill("a fresh weed patch, minted from the browser")
                expect(submit).to_be_enabled()
                expect(page.get_by_test_id("run-cli-verb")).to_contain_text(
                    "blizzard hub routine run gardening-e2e-routine --scope gardening-e2e-fresh --mode full"
                )

                submit.click()

                # --- The confirmation names the real chunk id and links to the board -----
                confirmation = page.get_by_test_id("run-confirmation")
                expect(confirmation).to_be_visible()
                chunk_id_el = page.get_by_test_id("run-confirmation-chunk-id")
                expect(chunk_id_el).to_be_visible()
                chunk_id = chunk_id_el.text_content()
                assert chunk_id and chunk_id.startswith("ch_"), f"confirmation named no real chunk id: {chunk_id!r}"
                board_link = page.get_by_test_id("run-confirmation-board-link")
                expect(board_link).to_have_attribute("href", f"/board/chunk/{chunk_id}")
                expect(dialog.locator("fleet-board, [data-testid='board']")).to_have_count(0)

                # The mint really landed, before the run — a live hub read, not merely a
                # rendered claim.
                minted_scope = hub.get("/api/scopes")
                assert minted_scope.status_code == 200, minted_scope.text
                slugs = {s["slug"] for s in minted_scope.json()}
                assert "gardening-e2e-fresh" in slugs, f"the new scope never reached the hub: {slugs}"

                # --- Done closes the dialog back to the routines list ---------------------
                page.get_by_test_id("run-dialog-done").click()
                expect(dialog).to_have_count(0)
                expect(row).to_be_visible()
            finally:
                browser.close()

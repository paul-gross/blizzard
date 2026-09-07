"""The gardening run dialog, in a real browser (blizzard#399 D6): a real Chromium
(Playwright) opens it from the selected routine's own panel, proving the related-set
picker (D6) — no free-text mint — and the never-swept delta-steering (D5) round-trip
against a live hub. No runner or forge traffic: the routine's run mints a queued
chunk, never executed here. Needs the built bundle (``mise run web-build``)."""

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
    """Opens the run dialog off the selected routine's own panel, proves it offers only
    the routine's own related set (D6) — its own default plus a scope linked ahead of
    time, no free-text mint — and lands on the confirmation naming a real chunk id and
    linking to the board — against a live hub, no fixtures."""
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

        # A related scope, linked ahead of time — the dialog offers no mint of its own,
        # so a related-but-not-default scope must already exist and already be linked.
        scope_created = hub.post(
            "/api/scopes", json={"slug": "gardening-e2e-related", "description": "a related weed patch"}
        )
        assert scope_created.status_code == 201, scope_created.text
        scope_linked = hub.put(f"/api/routines/{routine_id}/scopes/gardening-e2e-related")
        assert scope_linked.status_code == 204, scope_linked.text
        # An unrelated scope — never linked into the routine's own set — proves the
        # dialog's picker narrows to the related set rather than every live scope.
        unrelated_created = hub.post(
            "/api/scopes", json={"slug": "gardening-e2e-unrelated", "description": "not this routine's own"}
        )
        assert unrelated_created.status_code == 201, unrelated_created.text

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            expect.set_options(timeout=20_000)
            try:
                # --- The routine list selects, the panel triggers the run ----------------
                page.goto(f"http://127.0.0.1:{hub_port}/gardening/routines", wait_until="load")
                row = page.get_by_test_id("gardening-routine-row-gardening-e2e-routine")
                expect(row).to_be_visible()
                row.click()
                expect(page.get_by_test_id("gardening-routine-record")).to_contain_text("gardening-e2e-routine")

                page.get_by_test_id("gardening-routine-run").click()
                dialog = page.get_by_test_id("gardening-run-dialog")
                expect(dialog).to_be_visible()
                expect(page.get_by_test_id("run-dialog-title")).to_contain_text("gardening-e2e-routine")

                # --- The dialog offers only the routine's related set (D6) ---------------
                expect(page.get_by_test_id("run-scope-option-gardening-e2e-default")).to_be_visible()
                expect(page.get_by_test_id("run-scope-option-gardening-e2e-related")).to_be_visible()
                expect(page.get_by_test_id("run-scope-option-gardening-e2e-unrelated")).to_have_count(0)
                expect(page.get_by_test_id("run-scope-option-new")).to_have_count(0)

                # --- The never-swept default scope steers to full (D5) -------------------
                page.get_by_test_id("run-scope-option-gardening-e2e-default").click()
                expect(page.get_by_test_id("run-mode-never-swept")).to_be_visible()
                expect(page.get_by_test_id("run-mode-delta")).to_be_disabled()

                # --- Switching to the related, linked scope and submitting ---------------
                page.get_by_test_id("run-scope-option-gardening-e2e-related").click()
                submit = page.get_by_test_id("run-dialog-submit")
                expect(submit).to_be_enabled()
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

                # The run really landed against the related scope the operator picked —
                # a live hub read, not merely a rendered claim.
                run_delta = hub.get(f"/api/runs/{chunk_id}")
                assert run_delta.status_code == 200, run_delta.text
                assert run_delta.json()["scope_slug"] == "gardening-e2e-related", run_delta.json()

                # --- Done closes the dialog back to the routines list ---------------------
                page.get_by_test_id("run-dialog-done").click()
                expect(dialog).to_have_count(0)
                expect(row).to_be_visible()
            finally:
                browser.close()

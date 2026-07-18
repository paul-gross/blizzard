"""Board cost/usage live-over-SSE e2e — scenario 7 of the standing e2e smoke (issue #60).

The browser half of the cost/usage feature: a **real Chromium**, driven by Playwright,
over the **served mission-control board** (``blizzard hub host`` mounts the built Angular
app at ``/``) wired to a live hub over a minted ``blizzard-mock`` fixture. Scenario 6
(``test_board_browser_e2e``) proves the operator *controls* live over SSE; this scenario
proves the **cost/usage figures** the P4 render adds are (a) rendered end to end from a
real ``GET /api/chunks`` + ``GET /api/spend`` off the live hub, and (b) **updated
live over SSE with no reload** when a fresh ``usage.recorded`` fact lands — the one claim
only a real browser over the real SSE spine can make, and the one the component tiers
(``chunk-detail-panel.spec.ts``, ``board-header.spec.ts``, ``board-shell.spec.ts``,
``fleet-live.spec.ts``, ``test_usage_facts_ingest.py``) each prove only a slice of.

The live path exercised: a usage fact arrives at ``POST /api/fleet/events`` (``kind:
usage.recorded``), the hub re-broadcasts ``chunk-changed`` over ``GET /api/events/stream``
(SSE), the ``FleetLiveUpdates`` spine invalidates the chunk read **and** the fleet
spend-since read (``hubFleetSpendKey``), and the board re-derives — the card's cost badge,
the header's spend-today figure, and the open detail dock's total all move **in place**.

No runner is driven: the chunk is claimed straight through ``POST /api/fleet/routes`` (the same
claim the runner's FILL makes) so it derives ``running`` and shows on the board, then usage
facts are pushed straight through the hub's own ``POST /api/fleet/events`` — the runner's real
store-and-forward destination. That keeps the scenario about the *render + SSE* surface
this phase added, not a re-proof of the reconciliation loop scenario 6 already carries.

It is the **e2e tier**: it needs the full live stack, the sibling ``blizzard-mock``
worktree, a local winter source, and an installed Chromium, so it is **skipped unless
``BLIZZARD_E2E=1``** and those are present. Reproduce it — from the ``blizzard``
worktree in a provisioned feature env — with::

    uv run playwright install chromium   # once, out of band
    BLIZZARD_E2E=1 uv run pytest tests/e2e/test_board_cost_live_e2e.py
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import httpx
import pytest

from tests.e2e.test_acceptance_loop import (
    FIXTURE_ENV,
    REPO,
    REPO_NAME,
    _forge,
    _free_port,
    _graph_yaml,
    _hub,
    _mock_bin_dir,
    _winter_source,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e board cost/usage needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]


def _ingest_promote_claim(forge: httpx.Client, hub: httpx.Client, title: str) -> tuple[str, str]:
    """File an issue, ingest it, promote it, and claim a route straight through the hub API.

    Returns ``(chunk_id, node_id)`` for a chunk that derives ``running`` (a live route) and
    so renders as a card on the board — with a node the usage facts below attach to.
    """
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "cost render chunk"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    node_id = claim.json()["envelope"]["node"]["node_id"]
    return chunk_id, node_id


def _push_usage(
    hub: httpx.Client,
    *,
    chunk_id: str,
    node_id: str,
    seq: int,
    cost_usd: float | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_create_tokens: int = 0,
) -> None:
    """Push one ``usage.recorded`` fact through the hub's real store-and-forward endpoint."""
    payload = {
        "chunk_id": chunk_id,
        "node_id": node_id,
        "epoch": 1,
        "kind": "spawn",
        "model": "claude-opus-4-8",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_create_tokens": cache_create_tokens,
        "cost_usd": cost_usd,
    }
    resp = hub.post(
        "/api/fleet/events",
        json={"runner_id": "r1", "facts": [{"seq": seq, "kind": "usage.recorded", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text


def test_board_renders_cost_and_updates_live_over_sse(tmp_path: Path, chromium_available: bool) -> None:
    """Cost/usage renders on the board + chunk detail and updates live over SSE (issue #60)."""
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    from playwright.sync_api import expect, sync_playwright

    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    winter_source = _winter_source()
    if winter_source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    # 1. Mint a fresh, disposable fixture world (bare origins + a forge over them).
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
    origins = scratch / FIXTURE_ENV / "origins"

    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
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

                # Before any usage: the fleet spend read resolves to zero (not partial), and
                # the card carries no cost badge (costUsd 0, not partial). This is the
                # baseline the live SSE update must visibly move off.
                expect(page.get_by_test_id("spend-today-value")).to_have_text("$0.00")
                expect(card.get_by_test_id("card-cost")).to_have_count(0)

                # --- A usage fact lands at the hub → chunk-changed over SSE → the board
                #     re-derives with NO reload. Playwright's assertions poll, so these
                #     succeed only once the SSE spine has invalidated and re-read. --------
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

                # The card's cost badge appears live, and the header spend-today figure
                # moves off zero live — both off the same fact, invalidated together
                # (hubChunkKey + hubFleetSpendKey).
                expect(card.get_by_test_id("card-cost")).to_have_text("$0.42")
                expect(page.get_by_test_id("spend-today-value")).to_have_text("$0.42")

                # --- Detail dock: header total + expandable token breakdown -------------
                card.click()
                expect(page.get_by_test_id("chunk-detail")).to_be_visible()
                expect(page.get_by_test_id("cost-total-usd")).to_contain_text("$0.42")
                expect(page.get_by_test_id("cost-partial-badge")).to_have_count(0)

                # Collapsed by default: the total, not the per-class breakdown.
                expect(page.get_by_test_id("tokens-total")).to_contain_text("2.4k")
                expect(page.get_by_test_id("tokens-breakdown")).to_have_count(0)
                page.get_by_test_id("tokens-expand-toggle").click()
                expect(page.get_by_test_id("tokens-breakdown")).to_have_count(1)
                expect(page.get_by_test_id("tokens-input")).to_contain_text("1.2k")
                expect(page.get_by_test_id("tokens-output")).to_contain_text("800")
                expect(page.get_by_test_id("tokens-cache-read")).to_contain_text("300")
                expect(page.get_by_test_id("tokens-cache-create")).to_contain_text("100")

                # --- A cost-absent (crash/reap-path) usage fact makes the total a LOWER
                #     BOUND — the partial marker must appear live everywhere it renders,
                #     never a silently-understated exact figure. ---------------------------
                _push_usage(hub, chunk_id=chunk_id, node_id=node_id, seq=2, cost_usd=None)

                # Marked live, no reload: the detail total, the card badge, and the header
                # spend-today figure all gain the leading `~` (formatCost's lower-bound mark).
                expect(page.get_by_test_id("cost-partial-badge")).to_have_count(1)
                expect(page.get_by_test_id("cost-total-usd")).to_contain_text("~$0.42")
                expect(card.get_by_test_id("card-cost")).to_have_text("~$0.42")
                expect(page.get_by_test_id("spend-today-value")).to_have_text("~$0.42")
            finally:
                browser.close()

"""Board cost/usage live-over-SSE e2e — the `test_board_cost_live_e2e` scenario of the standing e2e smoke (issue #60).

A real Chromium (Playwright) over the served board wired to a live hub, asserting
cost/usage figures render end to end and update live with no reload when a
``usage.recorded`` fact lands. No runner is driven; skipped unless ``BLIZZARD_E2E=1``
and Chromium is installed (``uv run playwright install chromium``)."""

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
    estimated_cost_usd: float | None = None,
) -> None:
    """Push one ``usage.recorded`` fact through the hub's real store-and-forward endpoint.
    ``estimated_cost_usd`` is omitted from the payload, not sent as ``None``, when unused.
    """
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
    if estimated_cost_usd is not None:
        payload["estimated_cost_usd"] = estimated_cost_usd
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

                # The card's cost figure appears live, and the header spend-today figure
                # moves off zero live — both off the same fact. Neither carries a marker:
                # nothing here is estimated or partial yet.
                expect(card.get_by_test_id("card-cost")).to_have_text("$0.42")
                expect(page.get_by_test_id("spend-today-value")).to_have_text("$0.42")

                # --- Detail dock: header total + the token line --------------------------
                card.click()
                expect(page.get_by_test_id("chunk-detail")).to_be_visible()
                expect(page.get_by_test_id("cost-total-usd")).to_contain_text("$0.42")
                expect(page.get_by_test_id("cost-partial-badge")).to_have_count(0)

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
                expect(page.get_by_test_id("cost-partial-badge")).to_have_count(1)
                expect(page.get_by_test_id("cost-total-usd")).to_contain_text("$0.42+")
                expect(card.get_by_test_id("card-cost")).to_have_text("$0.42+")
                expect(page.get_by_test_id("spend-today-value")).to_have_text("$0.42+")

                # --- A second, subscription-only chunk: an estimate, no billed cost -------
                # A separate claim from the null-cost chunk above.
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

                # The header's spend-today figure folds in the fleet-wide combined total:
                # the first chunk's $0.42 billed and PARTIAL, plus this chunk's own $0.07
                # estimate — both markers on the one figure.
                expect(page.get_by_test_id("spend-today-value")).to_have_text("~$0.49+")

                # The detail dock: this chunk's own combined figure, marked `~` since it is
                # entirely estimated, and no partial marker — an estimate-only row does not
                # make this chunk PARTIAL.
                estimate_card.click()
                expect(page.get_by_test_id("cost-total-usd")).to_have_text("~$0.07")
                expect(page.get_by_test_id("cost-partial-badge")).to_have_count(0)
            finally:
                browser.close()

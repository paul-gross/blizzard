"""The runner panel's live SSE update — `test_runner_panel_live_e2e`.

A real Chromium over the runner's own panel, against a **real** `blizzard-runner host`
subprocess ticking its live loop, proving a real mutation reaches the panel with
no reload, inside the 1-minute poll backstop a pass could not otherwise satisfy.
Setup needs match `tests/e2e/test_acceptance_loop.py`'s module docstring."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fleet_launchers import (
    MOCK_HARNESS_FENCE_VAR,
    REPO,
    await_http,
    mock_bin_dir,
    run_forge,
    run_hub,
    runner_config,
    terminate,
)
from tests.fleet_world import FIXTURE_ENV, REPO_NAME, graph_yaml, mint_world
from tests.host_files import write_host_file
from tests.runner_join import runner_spawn_env
from tests.support import daemon_log_sink, free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e runner panel liveness needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]


def _runner_bin() -> str:
    return str(Path(sys.executable).parent / "blizzard-runner")


def test_runner_panel_updates_live_over_sse_with_no_reload(tmp_path: Path, chromium_available: bool) -> None:
    """A worker started, then ended, by the live loop renders on the panel's board with no
    reload — the runner counterpart of `test_board_browser_e2e`'s live-pause proof,
    over `runner-live-updates.ts`'s own registry rather than the hub's."""
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    from playwright.sync_api import expect, sync_playwright

    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")

    scratch = tmp_path / "scratch"
    mint_world(bin_dir, scratch)
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"

    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": graph_yaml()}).status_code == 201

        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "panel live update", "body": "prove SSE, no poll"})
        assert issue.status_code == 201, issue.text
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        # Left in `backlog` — promoting now would race the live loop's tick against
        # Chromium launch/page-load; promotion moves below, after the empty board is confirmed.

        # Persisted to disk, unlike the other scenarios' in-process config, so a real
        # `blizzard-runner host --dir` subprocess can load it.
        runner_dir = tmp_path / "runner"
        config = runner_config(runner_dir, workspace, bin_dir, hub_port)
        write_host_file(config)

        log = runner_dir / "daemon.log"
        env = runner_spawn_env(
            config.hub_url,
            {
                MOCK_HARNESS_FENCE_VAR: "1",
                # A fast tick keeps this scenario's own observation window short.
                "BZ_RUNNER_TICK_SECONDS": "1",
            },
        )
        proc = subprocess.Popen(
            [_runner_bin(), "host", "--dir", str(runner_dir), "--host", config.host, "--port", str(config.port)],
            env=env,
            stdout=daemon_log_sink(log),
            stderr=subprocess.STDOUT,
            text=True,
        )
        runner_client = None
        try:
            import httpx

            runner_client = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=15.0)
            await_http(proc, runner_client, "/api/health", log=log)
            # The panel's board, across every runner the host serves — this one alone.
            board_url = f"http://{config.host}:{config.port}/board"

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                expect.set_options(timeout=15_000)
                try:
                    # Load the panel ONCE, never reloaded again. Established pre-state, not a
                    # race: nothing is promoted yet, so the empty board holds regardless of launch time.
                    page.goto(board_url, wait_until="load")
                    expect(page.get_by_test_id("local-panel")).to_be_visible()
                    card = page.locator(f'[data-testid="chunk-row"][data-chunk-id="{chunk_id}"]')
                    expect(card).to_have_count(0)

                    # NOW promote — FILL mints a real lease-changed(created) frame the
                    # panel's 1-minute backstop cannot explain, not a fixture shortcut: the
                    # chunk's card arriving inside the 15s wait is the SSE proof. The mock
                    # worker exits at once, so the card's live worker line is not waited on.
                    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
                    expect(card).to_have_count(1)

                    # build -> review -> deliver -> done under the same tick; this wait's own
                    # timeout equals the same backstop, so unlike the card's arrival it alone
                    # doesn't prove SSE. The ended chunk stays on the board, in the Done lane.
                    expect(card).to_have_attribute("data-lane", "done", timeout=60_000)
                    expect(card.get_by_test_id("chunk-row-worker")).to_have_count(0)
                finally:
                    browser.close()
        finally:
            if runner_client is not None:
                runner_client.close()
            terminate(proc)

"""The runner panel's live SSE update — `test_runner_panel_live_e2e` (blizzard#317 Phase 5).

A real Chromium over the runner's own panel, against a **real** `blizzard-runner host`
subprocess ticking its live loop (D2), proving a real mutation reaches the panel with
no reload, inside the 1-minute poll backstop (D7) a pass could not otherwise satisfy.
Setup needs match `tests/e2e/test_acceptance_loop.py`'s module docstring."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.test_acceptance_loop import (
    FIXTURE_ENV,
    MOCK_HARNESS_FENCE_VAR,
    REPO,
    REPO_NAME,
    _await_http,
    _forge,
    _free_port,
    _graph_yaml,
    _hub,
    _mock_bin_dir,
    _runner_config,
    _terminate,
    _winter_source,
)
from tests.support import daemon_log_sink

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
    """A lease minted, then closed, by the live loop renders on the panel with no
    reload — the runner counterpart of `test_board_browser_e2e`'s live-pause proof,
    over `runner-live-updates.ts`'s own registry rather than the hub's."""
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    from playwright.sync_api import expect, sync_playwright

    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    winter_source = _winter_source()
    if winter_source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

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
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    (workspace / ".blizzard-mock-harness-fence").write_text("e2e fence marker\n")

    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201

        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "panel live update", "body": "prove SSE, no poll"})
        assert issue.status_code == 201, issue.text
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        # Left in `backlog` — promoting now would race the live loop's tick against
        # Chromium launch/page-load; promotion moves below, after "0 live" is confirmed.

        # Persisted to disk, unlike the other scenarios' in-process config, so a real
        # `blizzard-runner host --dir` subprocess can load it.
        runner_dir = tmp_path / "runner"
        config = _runner_config(runner_dir, workspace, bin_dir, hub_port)
        config.config_path.write_text(config.to_toml())

        log = runner_dir / "daemon.log"
        env = {
            **os.environ,
            MOCK_HARNESS_FENCE_VAR: "1",
            # A fast tick keeps this scenario's own observation window short.
            "BZ_RUNNER_TICK_SECONDS": "1",
        }
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
            _await_http(proc, runner_client, "/api/health", log=log)

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                expect.set_options(timeout=15_000)
                try:
                    # Load the panel ONCE, never reloaded again. Established pre-state, not a
                    # race: nothing is promoted yet, so "0 live" holds regardless of launch time.
                    page.goto(f"http://{config.host}:{config.port}/", wait_until="load")
                    expect(page.get_by_test_id("local-panel")).to_be_visible()
                    expect(page.get_by_test_id("lease-count")).to_have_text("0 live")
                    expect(page.get_by_test_id("agent-row")).to_have_count(0)

                    # NOW promote — FILL mints a real lease-changed(created) frame the
                    # panel's 1-minute backstop (D7) cannot explain, not a fixture shortcut.
                    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
                    expect(page.get_by_test_id("lease-count")).to_have_text("1 live")
                    expect(page.get_by_test_id("agent-row")).to_have_count(1)

                    # build -> review -> deliver -> done under the same tick; this wait's own
                    # timeout equals the D7 backstop, so unlike "1 live" it alone doesn't prove SSE.
                    expect(page.get_by_test_id("lease-count")).to_have_text("0 live", timeout=60_000)
                    expect(page.get_by_test_id("agent-row")).to_have_count(0)
                finally:
                    browser.close()
        finally:
            if runner_client is not None:
                runner_client.close()
            _terminate(proc)

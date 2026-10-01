"""A packaged triage migration selects its producing step on the served hub board."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests.e2e.test_acceptance_loop import _forge, _free_port, _hub, _mock_bin_dir, _winter_source
from tests.e2e.test_transcript_tab_browser_e2e import _ingest, _mint

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("BLIZZARD_E2E") != "1", reason="requires live e2e stack"),
]


def test_triage_migration_selects_transcript_and_artifact(tmp_path: Path, chromium_available: bool) -> None:
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed")
    from playwright.sync_api import expect, sync_playwright

    bin_dir, winter_source = _mock_bin_dir(), _winter_source()
    if bin_dir is None or winter_source is None:
        pytest.skip("requires provisioned mock worktree and local winter source")
    origins = _mint(bin_dir, winter_source, tmp_path / "scratch")
    forge_port, hub_port = _free_port(), _free_port()

    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        sync = hub.post("/api/graphs/sync")
        assert sync.status_code == 200 and sync.json()["ok"], sync.text
        chunk_id = _ingest(forge, hub, "triage migration history")
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
        claim = hub.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
        )
        assert claim.status_code == 201, claim.text
        node_id = claim.json()["envelope"]["node"]["node_id"]
        assert claim.json()["envelope"]["node"]["node_name"] == "triage"
        lease = hub.post(
            "/api/fleet/events",
            json={
                "runner_id": "r1",
                "facts": [{"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 1}}],
            },
        )
        assert lease.status_code == 200, lease.text
        completed = hub.post(
            f"/api/fleet/chunks/{chunk_id}/completions",
            json={
                "choice": "basic",
                "epoch": 1,
                "runner_id": "r1",
                "from_node_id": node_id,
                "artifacts": [{"name": "triage-findings", "kind": "asset", "content": "basic lane rationale"}],
            },
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["outcome"] == "migrated"
        shipped = hub.post(
            "/api/fleet/transcripts",
            json={
                "runner_id": "r1",
                "records": [
                    {
                        "seq": 1,
                        "segment_id": "sg_triage",
                        "chunk_id": chunk_id,
                        "node_id": node_id,
                        "epoch": 1,
                        "spawn_generation": 1,
                        "turn_range_start": 0,
                        "turn_range_end": 1,
                        "final": True,
                        "normalizer_version": "v1",
                        "harness_version": "claude-code-1.0",
                        "turns": [
                            {
                                "index": 0,
                                "kind": "asst",
                                "timestamp": None,
                                "text": "triage chose basic lane",
                                "tool": None,
                                "thinking_redacted": False,
                                "sidechain": None,
                                "truncated": False,
                            }
                        ],
                    }
                ],
            },
        )
        assert shipped.status_code == 200, shipped.text
        assert shipped.json()["applied"] == [1]
        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        assert detail["migrations"][0]["source"] == "authored-edge"
        assert (detail["migrations"][0]["from_node_id"], detail["migrations"][0]["epoch"]) == (node_id, 1)
        assert any(
            a["name"] == "triage-findings" and (a["node_id"], a["epoch"]) == (node_id, 1) for a in detail["artifacts"]
        )

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            failures: list[str] = []
            page.on(
                "response",
                lambda response: (
                    failures.append(f"{response.status} {response.url}")
                    if response.status >= 400 and "/api/" in response.url
                    else None
                ),
            )
            expect.set_options(timeout=20_000)
            try:
                for width in (1280, 390):
                    page.set_viewport_size({"width": width, "height": 900})
                    page.goto(f"http://127.0.0.1:{hub_port}/board/chunk/{chunk_id}?tab=node-history", wait_until="load")
                    row = page.get_by_test_id("selection-migration-step")
                    expect(row).to_have_attribute("data-step-key", f"{node_id}:1")
                    expect(row).to_have_attribute("role", "button")
                    row.click()
                    expect(page).to_have_url(re.compile(rf"[?&]step={node_id}(?:%3A|:)1"))
                    expect(page.get_by_test_id("node-history-transcript-body")).to_contain_text(
                        "triage chose basic lane"
                    )
                    expect(page.get_by_test_id("node-history-artifact-content")).to_contain_text("basic lane rationale")
                    expect(page.get_by_test_id("node-history-artifact-key")).to_contain_text("triage-findings")
                    if width == 390:
                        page.get_by_test_id("node-history-back").click()
                        expect(page.get_by_test_id("selection-migration-step")).to_be_visible()
                assert not failures, failures
            finally:
                browser.close()

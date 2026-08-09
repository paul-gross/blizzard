"""The chunk board's Transcripts tab, browser e2e (blizzard#248 Phase 3).

Seeds segments through ``POST /api/fleet/transcripts`` as a runner principal — the path
``tests/service/test_transcript_segments_service.py`` already drives, since the runner's
own shipping lane (#246) is undelivered and no runner spawns here. Skipped unless
``BLIZZARD_E2E=1`` with the sibling ``blizzard-mock`` worktree provisioned and Chromium."""

from __future__ import annotations

import os
import re
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
    _hub,
    _mock_bin_dir,
    _winter_source,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e transcript tab browser needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]


def _mint(bin_dir: Path, winter_source: Path, scratch: Path) -> Path:
    """Mint a fresh, disposable fixture world — only its forge-registered repo matters
    here, since no runner (and so no workspace/harness fence) is ever spawned."""
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
    return scratch / FIXTURE_ENV / "origins"


def _ingest(forge: httpx.Client, hub: httpx.Client, title: str) -> str:
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "the chunk"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    return str(ingested.json()["chunk_id"])


def _segment_one(chunk_id: str) -> dict:
    """The step's first segment: a thinking turn, and a tool call whose sidechain nests
    one turn — exercises the collapsed-by-default thinking turn and a nested sidechain."""
    return {
        "seq": 1,
        "segment_id": "sg_1",
        "chunk_id": chunk_id,
        "node_id": "nd_build",
        "epoch": 1,
        "spawn_generation": 0,
        "turn_range_start": 0,
        "turn_range_end": 2,
        "final": True,
        "normalizer_version": "v1",
        "harness_version": "claude-code-1.0",
        "turns": [
            {
                "index": 0,
                "kind": "thinking",
                "timestamp": None,
                "text": "weighing two approaches before committing to one",
                "tool": None,
                "thinking_redacted": False,
                "sidechain": None,
                "truncated": False,
            },
            {
                "index": 1,
                "kind": "tool",
                "timestamp": None,
                "text": "",
                "tool": {
                    "name": "Task",
                    "input": {"prompt": "survey the callers"},
                    "input_unparsed": None,
                    "input_shape": "object",
                    "tool_use_id": "t1",
                    "output": "3 callers found",
                    "output_truncated": False,
                },
                "thinking_redacted": False,
                "sidechain": {
                    "agent_id": "agent-1",
                    "agent_type": "explorer",
                    "link": "prompt-timestamp",
                    "turns": [
                        {
                            "index": 0,
                            "kind": "asst",
                            "timestamp": None,
                            "text": "surveying callers now",
                            "tool": None,
                            "thinking_redacted": False,
                            "sidechain": None,
                            "truncated": False,
                        }
                    ],
                },
                "truncated": False,
            },
        ],
    }


def _segment_two(chunk_id: str) -> dict:
    """The step's second segment (``spawn_generation`` 1, so it continues the first) —
    carries an *unlinked* sidechain, opened standalone in the scenario."""
    return {
        "seq": 2,
        "segment_id": "sg_2",
        "chunk_id": chunk_id,
        "node_id": "nd_build",
        "epoch": 1,
        "spawn_generation": 1,
        "turn_range_start": 2,
        "turn_range_end": 3,
        "final": True,
        "normalizer_version": "v1",
        "harness_version": "claude-code-1.0",
        "turns": [
            {
                "index": 0,
                "kind": "sidechain",
                "timestamp": None,
                "text": "",
                "tool": None,
                "thinking_redacted": False,
                "sidechain": {
                    "agent_id": None,
                    "agent_type": None,
                    "link": "unlinked",
                    "turns": [
                        {
                            "index": 0,
                            "kind": "asst",
                            "timestamp": None,
                            "text": "an unlinked subagent's own conversation",
                            "tool": None,
                            "thinking_redacted": False,
                            "sidechain": None,
                            "truncated": False,
                        }
                    ],
                },
                "truncated": False,
            }
        ],
    }


def test_chunk_transcripts_tab_browser(tmp_path: Path, chromium_available: bool) -> None:
    """The chunk board's Transcripts tab, driven through a real browser end to end."""
    if not chromium_available:
        pytest.skip("no Playwright Chromium installed (run `uv run playwright install chromium`)")
    from playwright.sync_api import expect, sync_playwright

    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    winter_source = _winter_source()
    if winter_source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    origins = _mint(bin_dir, winter_source, tmp_path / "scratch")
    forge_port, hub_port = _free_port(), _free_port()

    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        chunk_id = _ingest(forge, hub, "transcript tab browser scenario")
        ack = hub.post(
            "/api/fleet/transcripts",
            json={"runner_id": "r1", "records": [_segment_one(chunk_id), _segment_two(chunk_id)]},
        )
        assert ack.status_code == 200, ack.text
        assert ack.json()["applied"] == [1, 2]

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            expect.set_options(timeout=20_000)
            try:
                page.goto(f"http://127.0.0.1:{hub_port}/board/chunk/{chunk_id}", wait_until="load")
                expect(page.get_by_test_id("board-chunk-detail")).to_be_visible()

                page.get_by_test_id("tab-transcripts").click()
                expect(page.get_by_test_id("chunk-transcripts-tab")).to_be_visible()
                # No transition or lease here, so both segments' shared `(node_id, epoch)`
                # matches no history row — the tab's *unmatched* bucket (D5, `review:F9`).
                expect(page.get_by_test_id("transcript-step")).to_have_count(1)
                expect(page.get_by_test_id("transcript-step")).to_contain_text("unmatched")

                # Open the step's first segment.
                page.locator('[data-testid="transcript-segment-item"][data-segment-id="sg_1"]').click()
                expect(page.get_by_test_id("transcript-segment-body")).to_be_visible()

                # Expand the thinking turn, collapsed by default.
                thinking = page.locator(".turn.k-thinking .thinking")
                thinking.locator("summary").click()
                expect(thinking.locator(".th-body")).to_have_text("weighing two approaches before committing to one")

                # The nested sidechain is inline under its spawning tool call.
                expect(page.get_by_test_id("transcript-sidechain-nested")).to_contain_text("surveying callers now")

                # Follow the continues-in link to the second segment.
                page.get_by_test_id("transcript-continues-in").click()
                expect(page.locator('[data-testid="transcript-segment-item"][data-segment-id="sg_2"]')).to_have_class(
                    re.compile("active")
                )

                # Open its unlinked sidechain standalone, then back to the segment.
                page.get_by_test_id("transcript-sidechain-open").click()
                expect(page.get_by_test_id("transcript-sidechain-back")).to_be_visible()
                expect(page.locator("body")).to_contain_text("an unlinked subagent's own conversation")
                page.get_by_test_id("transcript-sidechain-back").click()
                expect(page.get_by_test_id("transcript-sidechain-back")).to_have_count(0)

                # Follow the continued-from link back to the first segment.
                page.get_by_test_id("transcript-continued-from").click()
                expect(page.locator('[data-testid="transcript-segment-item"][data-segment-id="sg_1"]')).to_have_class(
                    re.compile("active")
                )
            finally:
                browser.close()

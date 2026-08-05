"""``blizzard-mock-data`` as a live hub-board seeder (``blizzard-mock#5``).

A real, migrated hub proves what the offline-tested mock-data CLI can't: seeded status
agrees with the hub's own status derivation, and the drift guard passes against the real
schema — hosted with zero work sources, writing live to the daemon's sqlite, no restart.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.e2e.test_acceptance_loop import _await_http, _free_port, _terminate
from tests.service.support import require_mock_fleet, service_gate
from tests.support import daemon_log_sink

pytestmark = [pytest.mark.service, service_gate]

_CHUNK_LINE = re.compile(r"^\s*chunk (\S+) status=(\S+)\s*$", re.MULTILINE)
_CEILING_PAUSED_RUNNER = re.compile(r"ceiling-paused: '([^']*)'")


def _require_mock_data_binary() -> Path:
    """The provisioned sibling ``blizzard-mock`` venv bin with ``blizzard-mock-data``,
    or skip.

    Distinct from :func:`~tests.service.support.require_mock_fleet`'s hub/runner probe:
    this module drives the mock-data seeder specifically."""
    bin_dir = require_mock_fleet()
    if not (bin_dir / "blizzard-mock-data").is_file():
        pytest.skip(
            "no provisioned sibling blizzard-mock worktree with blizzard-mock-data (run `winter provision <env>`)"
        )
    return bin_dir


@contextlib.contextmanager
def _zero_work_source_hub(hub_dir: Path, port: int) -> Iterator[httpx.Client]:
    """Host a real hub with **zero** ``[[work_source]]`` blocks.

    Mirrors ``tests.e2e.test_acceptance_loop._hub`` minus ``write_work_sources`` — every
    chunk here is seeded directly, never ingested, so no work source is needed."""
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
    log = hub_dir / "daemon.log"
    proc = subprocess.Popen(
        [hub_bin, "host", "--dir", str(hub_dir), "--host", "127.0.0.1", "--port", str(port)],
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0)
    try:
        _await_http(proc, client, "/api/health", log=log)
        yield client
    finally:
        client.close()
        _terminate(proc)


def _mock_data(bin_dir: Path, *args: str) -> str:
    """Run one ``blizzard-mock-data`` invocation to completion and return its stdout.

    A short-lived one-shot CLI call, captured directly rather than sunk to a log file —
    this repo's own convention for one-shot subprocess calls."""
    result = subprocess.run([str(bin_dir / "blizzard-mock-data"), *args], check=True, capture_output=True, text=True)
    return result.stdout


def _parse_scenario_chunks(stdout: str) -> list[tuple[str, str]]:
    """Every ``  chunk <id> status=<status>`` line ``scenario board`` printed, in the
    order it printed them — the seeder's own intended-status map, straight from its
    stdout rather than re-derived from the ``i % 9`` distribution algorithm by hand."""
    return _CHUNK_LINE.findall(stdout)


def _parse_ceiling_paused_runner(stdout: str) -> str:
    match = _CEILING_PAUSED_RUNNER.search(stdout)
    assert match is not None, stdout
    return match.group(1)


def _chunks_by_id(hub: httpx.Client) -> dict[str, dict]:
    resp = hub.get("/api/chunks")
    assert resp.status_code == 200, resp.text
    return {row["chunk_id"]: row for row in resp.json()}


def _chunk_detail(hub: httpx.Client, chunk_id: str) -> dict:
    resp = hub.get(f"/api/chunks/{chunk_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_scenario_board_status_composition_agrees_with_the_hub_and_survives_a_concurrent_write(
    tmp_path: Path,
) -> None:
    bin_dir = _require_mock_data_binary()
    hub_dir = tmp_path / "hub"
    port = _free_port()

    with _zero_work_source_hub(hub_dir, port) as hub:
        # --- 1. seed via the real hub's own runtime dir, live, no restart ------------ #
        board_stdout = _mock_data(bin_dir, "scenario", "board", "--chunks", "9", "--stress", "--dir", str(hub_dir))
        intended = _parse_scenario_chunks(board_stdout)
        # 9 base chunks + --stress's 2 extra chunks (scenario_seed.py's own module docstring).
        assert len(intended) == 11, board_stdout
        ceiling_runner_id = _parse_ceiling_paused_runner(board_stdout)

        # An explicit ready chunk with work refs (zero-work-source label/web_url-null
        # rendering) that also gets an answered-AND-delivered question.
        chunk_id = _mock_data(
            bin_dir,
            "create",
            "chunk",
            "--store",
            "hub",
            "--dir",
            str(hub_dir),
            "--status",
            "ready",
            "--work-ref",
            "demo-src#101",
            "--work-ref",
            "demo-src#202",
        ).strip()
        question_text = "mock-data-seeding-service: a delivered proof question"
        _mock_data(
            bin_dir,
            "create",
            "question",
            "--store",
            "hub",
            "--dir",
            str(hub_dir),
            "--chunk",
            chunk_id,
            "--text",
            question_text,
            "--option",
            "yes",
            "--option",
            "no",
            "--answer",
            "yes",
            "--answered-by",
            "operator",
            "--delivered",
        )

        # --- 2. GET /api/chunks — status-by-status agreement, no restart ------------- #
        actual = _chunks_by_id(hub)
        assert len(actual) == len(intended) + 1, actual  # 11 scenario chunks + the explicit one above
        for seeded_chunk_id, intended_status in intended:
            assert seeded_chunk_id in actual, (seeded_chunk_id, sorted(actual))
            assert actual[seeded_chunk_id]["status"] == intended_status, (
                seeded_chunk_id,
                intended_status,
                actual[seeded_chunk_id],
            )

        # --- 3. GET /api/chunks/{id} — cost-partial, multi-question, delivered, refs - #

        # Chunk 0 (the first "ready" entry) is the guaranteed cost-partial one
        # (scenario_seed.py's module docstring).
        cost_partial_chunk_id, cost_partial_status = intended[0]
        assert cost_partial_status == "ready", intended[0]
        cost_partial_detail = _chunk_detail(hub, cost_partial_chunk_id)
        assert cost_partial_detail["cost"]["cost_partial"] is True, cost_partial_detail["cost"]
        assert cost_partial_detail["cost"]["cost_usd"] == 0.0, cost_partial_detail["cost"]
        assert cost_partial_detail["cost"]["input_tokens"] == 400, cost_partial_detail["cost"]
        assert cost_partial_detail["cost"]["output_tokens"] == 90, cost_partial_detail["cost"]

        # Position 9 (0-indexed) is --stress's extra multi-question waiting_on_human
        # chunk; position, not status, disambiguates it from position 3's single question.
        multi_question_chunk_id, multi_question_status = intended[9]
        assert multi_question_status == "waiting_on_human", intended[9]
        multi_question_detail = _chunk_detail(hub, multi_question_chunk_id)
        assert len(multi_question_detail["questions"]) == 3, multi_question_detail["questions"]
        assert all(not q["answered"] for q in multi_question_detail["questions"]), multi_question_detail["questions"]

        # The explicit chunk above: work refs render with null label/web_url (no
        # configured work source), and its question trail shows answer -> delivery.
        explicit_detail = _chunk_detail(hub, chunk_id)
        assert explicit_detail["status"] == "ready", explicit_detail
        work_refs = sorted(explicit_detail["work_refs"], key=lambda r: r["ref"])
        assert [r["ref"] for r in work_refs] == ["101", "202"], work_refs
        for ref in work_refs:
            assert ref["source"] == "demo-src", ref
            assert ref["label"] is None, ref
            assert ref["web_url"] is None, ref
        delivered_questions = [q for q in explicit_detail["questions"] if q["question"] == question_text]
        assert len(delivered_questions) == 1, explicit_detail["questions"]
        delivered = delivered_questions[0]
        assert delivered["answered"] is True, delivered
        assert delivered["answer"] == "yes", delivered
        assert delivered["answered_by"] == "operator", delivered
        assert delivered["delivered"] is True, delivered
        assert delivered["delivered_at"] is not None, delivered

        # --- 4. GET /api/events — mixed kinds/severities from the scenario's own log - #
        events_resp = hub.get("/api/events", params={"limit": 1000})
        assert events_resp.status_code == 200, events_resp.text
        events = events_resp.json()["events"]
        kinds = {e["kind"] for e in events}
        severities = {e["severity"] for e in events}
        assert {"runner.registered", "runner.paused", "chunk.escalated", "scenario.seeded"} <= kinds, kinds
        assert {"info", "warning", "critical"} <= severities, severities

        # --- 5. GET /api/runners — the ceiling-paused runner + the long stress identity #
        runners_resp = hub.get("/api/runners")
        assert runners_resp.status_code == 200, runners_resp.text
        runners = {r["runner_id"]: r for r in runners_resp.json()["runners"]}
        assert ceiling_runner_id in runners, sorted(runners)
        paused_runner = runners[ceiling_runner_id]
        assert paused_runner["locally_paused"] is True, paused_runner
        assert paused_runner["locally_paused_by"] == "mock-data", paused_runner
        assert paused_runner["locally_paused_reason"] is not None
        assert "spend ceiling" in paused_runner["locally_paused_reason"], paused_runner

        # --stress's long-identity runner: present, neither truncated nor erroring — every
        # ordinary base runner id is short, so a very long one is unambiguously the extra.
        long_identity_runners = [rid for rid in runners if len(rid) > 100]
        assert len(long_identity_runners) == 1, sorted(runners)
        long_runner = runners[long_identity_runners[0]]
        assert long_runner["workspace_id"] == "workspace-stress", long_runner

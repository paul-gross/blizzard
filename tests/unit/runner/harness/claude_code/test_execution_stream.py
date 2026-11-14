"""The Claude Code worker's ``stream-json`` stdout, held to the recorded corpus: every stdout reader, and the
execution evidence ``observe_execution`` reads off it."""

from __future__ import annotations

import os
from concurrent.futures import Executor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.runner.harness.claude_code.adapter import ResultEnvelope
from blizzard.runner.harness.claude_code.execution_stream import (
    EVIDENCE_FRESHNESS,
    StreamSnapshot,
    observe_stream,
)
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.observation import (
    ExecutionActivity,
    ExecutionLiveness,
    ExecutionObservationRequest,
)
from tests.support.claude_code_adapter import adapter as _adapter
from tests.support.repo_files import repo_root

_CORPUS = repo_root() / "src" / "blizzard" / "runner" / "harness" / "contracts" / "claude_code"
_RECORDED = sorted(_CORPUS.glob("*/*.jsonl"))
_NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


def _snapshot(lines: list[str], *, written_at: datetime = _NOW) -> StreamSnapshot:
    text = "\n".join(lines) + "\n"
    return StreamSnapshot(head=text.split("\n", 1)[0], tail=text, written_at=written_at)


def _through_first(lines: list[str], marker: str) -> list[str]:
    """The stream as it stood on disk right after the first line holding ``marker`` was written."""
    index = next(i for i, line in enumerate(lines) if marker in line)
    return lines[: index + 1]


def test_the_corpus_holds_both_probed_versions_main_and_subagent() -> None:
    assert sorted(f"{p.parent.name}/{p.name}" for p in _RECORDED) == [
        "2.1.322/main_tool_heartbeat.jsonl",
        "2.1.322/subagent_tool_heartbeat.jsonl",
        "2.1.325/main_tool_heartbeat.jsonl",
        "2.1.325/subagent_tool_heartbeat.jsonl",
    ]


# --------------------------------------------------------------------------- #
# Every stdout reader keeps working on a recorded stream.


@pytest.mark.parametrize("recorded", _RECORDED, ids=lambda p: f"{p.parent.name}-{p.stem}")
def test_the_envelope_readers_find_the_streams_result(recorded: Path, spawn_executor: Executor) -> None:
    output = recorded.read_text()
    adapter = _adapter(spawn_executor)

    envelope = ResultEnvelope.of(output)

    assert envelope is not None
    assert envelope.fields.get("type") == "result"
    assert envelope.result.strip() == "ok"
    assert adapter.has_usable_output(output) is True
    assert adapter.parse_verdict(output) is None
    assert adapter.parse_assessment(output) == ""
    sample = adapter.parse_usage(output, "spawn")
    assert sample is not None
    assert sample.model.startswith("claude-haiku")
    assert sample.output_tokens > 0
    assert sample.cost_usd is not None and sample.cost_usd > 0
    assert adapter.needs_usage_transcript(output) is False


@pytest.mark.parametrize("recorded", _RECORDED, ids=lambda p: f"{p.parent.name}-{p.stem}")
def test_a_stream_cut_before_its_result_has_no_usable_output(recorded: Path, spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor)
    lines = _lines(recorded)
    # A killed worker: every event but the result, its last line torn mid-write.
    cut = "\n".join(lines[:-2]) + "\n" + lines[-2][: len(lines[-2]) // 2]

    assert ResultEnvelope.of(cut) is None
    assert adapter.has_usable_output(cut) is False
    assert adapter.parse_usage(cut, "spawn") is None


@pytest.mark.parametrize("recorded", _RECORDED, ids=lambda p: f"{p.parent.name}-{p.stem}")
def test_the_classifiers_read_no_limit_or_overload_off_a_clean_stream(recorded: Path, spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor)
    output = recorded.read_text()

    assert adapter.classify_usage_limit(output, [], _NOW) is None
    assert adapter.classify_provider_overload(output, []) is None


# --------------------------------------------------------------------------- #
# Execution evidence over the recorded streams.


@pytest.mark.parametrize("recorded", _RECORDED, ids=lambda p: f"{p.parent.name}-{p.stem}")
def test_mid_tool_each_heartbeat_reads_alive_tool_with_the_write_time_as_evidence(recorded: Path) -> None:
    lines = _lines(recorded)
    written_at = _NOW - timedelta(seconds=5)
    for index, line in enumerate(lines):
        if '"type":"tool_progress"' not in line:
            continue
        observation = observe_stream(_snapshot(lines[: index + 1], written_at=written_at), _NOW)

        assert observation.liveness is ExecutionLiveness.ALIVE
        assert observation.activity is ExecutionActivity.TOOL
        assert observation.evidence_at == written_at
        assert observation.progress_at is None


@pytest.mark.parametrize("recorded", _RECORDED, ids=lambda p: f"{p.parent.name}-{p.stem}")
def test_a_pending_tool_use_before_any_heartbeat_reads_tool(recorded: Path) -> None:
    observation = observe_stream(_snapshot(_through_first(_lines(recorded), '"type":"tool_use"')), _NOW)

    assert observation.liveness is ExecutionLiveness.ALIVE
    assert observation.activity is ExecutionActivity.TOOL


@pytest.mark.parametrize("recorded", _RECORDED, ids=lambda p: f"{p.parent.name}-{p.stem}")
def test_the_reply_after_the_tool_reads_model(recorded: Path) -> None:
    lines = _lines(recorded)

    observation = observe_stream(_snapshot(lines[:-1]), _NOW)

    assert observation.liveness is ExecutionLiveness.ALIVE
    assert observation.activity is ExecutionActivity.MODEL


@pytest.mark.parametrize("recorded", _RECORDED, ids=lambda p: f"{p.parent.name}-{p.stem}")
def test_after_the_result_reads_exited(recorded: Path) -> None:
    observation = observe_stream(_snapshot(_lines(recorded)), _NOW)

    assert observation.liveness is ExecutionLiveness.EXITED


def test_a_subagents_tool_is_covered_by_the_parents_agent_heartbeat() -> None:
    lines = _lines(_CORPUS / "2.1.325" / "subagent_tool_heartbeat.jsonl")
    beat = next(line for line in lines if '"type":"tool_progress"' in line)

    assert '"tool_name":"Agent"' in beat
    assert observe_stream(_snapshot(_through_first(lines, '"type":"tool_progress"')), _NOW).activity is (
        ExecutionActivity.TOOL
    )


def test_a_write_older_than_the_freshness_bound_is_unknown() -> None:
    lines = _through_first(_lines(_CORPUS / "2.1.325" / "main_tool_heartbeat.jsonl"), '"type":"tool_progress"')

    fresh = observe_stream(_snapshot(lines, written_at=_NOW - EVIDENCE_FRESHNESS), _NOW)
    stale = observe_stream(_snapshot(lines, written_at=_NOW - EVIDENCE_FRESHNESS - timedelta(seconds=1)), _NOW)

    assert fresh.liveness is ExecutionLiveness.ALIVE
    assert stale.liveness is ExecutionLiveness.UNKNOWN
    assert stale.evidence_at is None


def test_a_missing_file_is_unknown() -> None:
    assert observe_stream(None, _NOW).liveness is ExecutionLiveness.UNKNOWN


def test_a_truncated_file_is_unknown() -> None:
    first = _lines(_CORPUS / "2.1.325" / "main_tool_heartbeat.jsonl")[0]

    observation = observe_stream(_snapshot([first[: len(first) // 2]]), _NOW)

    assert observation.liveness is ExecutionLiveness.UNKNOWN


def test_a_json_envelope_without_a_stream_init_is_unknown() -> None:
    envelope = '{"type":"result","subtype":"success","is_error":false,"result":"ok","session_id":"s1"}'

    assert observe_stream(_snapshot([envelope]), _NOW).liveness is ExecutionLiveness.UNKNOWN


def test_a_version_older_than_the_oldest_recorded_heartbeat_is_unknown_and_says_so() -> None:
    lines = _through_first(_lines(_CORPUS / "2.1.322" / "main_tool_heartbeat.jsonl"), '"type":"tool_progress"')
    lines[0] = lines[0].replace('"claude_code_version":"2.1.322"', '"claude_code_version":"2.1.300"')

    observation = observe_stream(_snapshot(lines), _NOW)

    assert observation.liveness is ExecutionLiveness.UNKNOWN
    assert "predates tool heartbeats" in observation.diagnostic


# --------------------------------------------------------------------------- #
# The adapter reads the generation's own file.


def _request(path: Path | str) -> ExecutionObservationRequest:
    return ExecutionObservationRequest(
        session=SessionReference(harness_id="claude-code", session_id="sid"),
        generation=1,
        stdout_path=str(path),
        now=_NOW,
    )


def test_observe_execution_reads_the_generations_stdout_with_its_mtime(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    lines = _through_first(_lines(_CORPUS / "2.1.325" / "main_tool_heartbeat.jsonl"), '"type":"tool_progress"')
    stdout = tmp_path / "lease-1.stdout"
    stdout.write_text("\n".join(lines) + "\n")
    written_at = _NOW - timedelta(seconds=20)
    os.utime(stdout, (written_at.timestamp(), written_at.timestamp()))

    observation = _adapter(spawn_executor).observe_execution(_request(stdout))

    assert observation.liveness is ExecutionLiveness.ALIVE
    assert observation.activity is ExecutionActivity.TOOL
    assert observation.evidence_at == written_at


def test_observe_execution_without_a_stdout_file_is_unknown(tmp_path: Path, spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor)

    assert adapter.observe_execution(_request(tmp_path / "absent.stdout")).liveness is ExecutionLiveness.UNKNOWN
    assert adapter.observe_execution(_request("")).liveness is ExecutionLiveness.UNKNOWN

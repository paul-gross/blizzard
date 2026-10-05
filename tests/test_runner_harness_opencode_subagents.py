"""OpenCode subagent linking, timestamps, and descendant usage over the 1.18.32 capture."""

from __future__ import annotations

import json
from concurrent.futures import Executor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs

from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.opencode.adapter import OpenCodeAdapter
from blizzard.runner.harness.opencode.shapes import OpenCodePart, parse_session_export
from blizzard.runner.harness.opencode.transcript.export import OpenCodeExportError
from blizzard.runner.harness.opencode.transcript.normalizer import build_turns, child_candidate_of
from blizzard.runner.harness.opencode.transcript.transcript_source import OpenCodeTranscriptSource
from blizzard.runner.harness.opencode.usage.descendant_usage import OpenCodeDescendantUsage
from blizzard.runner.harness.opencode.usage.price_cache import OpenCodeModelPrice, OpenCodeRate
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.transcript import TranscriptErrorFactory
from tests.repo_files import repo_root
from tests.runner_fakes import FakeProbe
from tests.test_runner_harness_opencode_transcript import FakeExporter

pytestmark = pytest.mark.unit

_CAPTURE = repo_root() / "src" / "blizzard" / "runner" / "harness" / "contracts" / "opencode" / "1.18.32"
_LEGACY = repo_root() / "src" / "blizzard" / "runner" / "harness" / "contracts" / "opencode" / "1.18.25"

_ROOT = "ses_f607309eaffeslYGcw3HSov58q"
_CONTINUED_CHILD = "ses_f6070f70bffeyvhNqEiKNcjN1F"
_OTHER_CHILD = "ses_f6058d7ddffew6JulFmKVefJx1"


def _load(name: str) -> str:
    return (_CAPTURE / name).read_text()


def _capture_exporter() -> FakeExporter:
    return FakeExporter(
        {
            _ROOT: _load("root_export.json"),
            _CONTINUED_CHILD: _load(f"child_{_CONTINUED_CHILD}.json"),
            _OTHER_CHILD: _load(f"child_{_OTHER_CHILD}.json"),
        }
    )


def _generation(n: int) -> str:
    return _load(f"run_generation_{n}.jsonl")


def _adapter(spawn_executor: Executor, exporter: FakeExporter | None = None, **kwargs: Any) -> OpenCodeAdapter:
    probe = FakeProbe()
    return OpenCodeAdapter(
        worker_env=AllowlistedEnv.of(()),
        process=probe,
        launcher=ProcessLauncher(probe, executor=spawn_executor),
        descendant_usage=OpenCodeDescendantUsage(exporter) if exporter is not None else None,
        **kwargs,
    )


def _child_steps_in(session_id: str, windows: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """The capture's step-finish parts of ``session_id`` in ``windows``, read straight from the JSON."""
    export = json.loads(_load(f"child_{session_id}.json"))
    return [
        part
        for message in export["messages"]
        if any(a <= message["info"]["time"]["created"] <= b for a, b in windows)
        for part in message["parts"]
        if part["type"] == "step-finish"
    ]


def _tokens(parts: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    return (
        sum(p["tokens"]["input"] for p in parts),
        sum(p["tokens"]["output"] + p["tokens"]["reasoning"] for p in parts),
        sum(p["tokens"]["cache"]["read"] for p in parts),
        sum(p["tokens"]["cache"]["write"] for p in parts),
    )


def _root_steps(generation: int) -> list[dict[str, Any]]:
    events = [json.loads(line) for line in _generation(generation).splitlines()]
    return [e["part"] for e in events if e["type"] == "step_finish"]


def _task_windows(generation: int) -> dict[str, tuple[int, int]]:
    events = [json.loads(line) for line in _generation(generation).splitlines()]
    return {
        e["part"]["state"]["metadata"]["sessionId"]: (
            e["part"]["state"]["time"]["start"],
            e["part"]["state"]["time"]["end"],
        )
        for e in events
        if e["type"] == "tool_use" and e["part"]["tool"] == "task"
    }


# --- child_candidate_of -------------------------------------------------


def test_child_candidate_of_resolves_the_real_1_18_32_task_keys() -> None:
    export = parse_session_export(json.loads(_load("root_export.json")))
    tasks = [p for m in export.messages for p in m.parts if p.type == "tool" and p.tool == "task"]
    candidates = [child_candidate_of(p) for p in tasks]
    assert [c.session_id for c in candidates if c] == [_CONTINUED_CHILD, _CONTINUED_CHILD, _OTHER_CHILD]
    assert [c.agent_type for c in candidates if c] == ["wf-ice-carver", "wf-ice-carver", "wf-frontend-verifier"]


def test_child_candidate_of_still_resolves_the_legacy_1_18_25_spelling() -> None:
    export = parse_session_export(json.loads((_LEGACY / "child_session.json").read_text())["export"])
    task = next(p for m in export.messages for p in m.parts if p.type == "tool")
    candidate = child_candidate_of(task)
    assert candidate is not None
    assert (candidate.session_id, candidate.agent_type) == ("ses_child", "explorer")


def test_child_candidate_of_reads_absent_or_malformed_metadata_as_none() -> None:
    def part(state_extra: dict[str, Any]) -> OpenCodePart:
        return OpenCodePart.parse(
            {
                "id": "p",
                "sessionID": "s",
                "messageID": "m",
                "type": "tool",
                "callID": "c",
                "tool": "task",
                "state": {"status": "pending", "input": {}, **state_extra},
            }
        )

    assert child_candidate_of(part({})) is None
    assert child_candidate_of(part({"metadata": "nope"})) is None
    assert child_candidate_of(part({"metadata": {"sessionId": 7}})) is None


# --- timestamps ----------------------------------------------------------


def test_every_turn_of_the_capture_carries_a_utc_timestamp() -> None:
    for name in ("root_export.json", f"child_{_CONTINUED_CHILD}.json", f"child_{_OTHER_CHILD}.json"):
        export = parse_session_export(json.loads(_load(name)))
        turns, _ = build_turns(export.messages, admitted=None)
        assert turns
        assert all(t.timestamp is not None and t.timestamp.tzinfo is UTC for t in turns), name


def test_a_tool_turn_takes_its_state_start_and_a_part_without_time_falls_back_to_the_message() -> None:
    def message(parts: list[dict[str, Any]], created: Any = 1_790_000_000_000) -> dict[str, Any]:
        return {
            "info": {"id": "m1", "sessionID": "s", "role": "assistant", "time": {"created": created}},
            "parts": [{"sessionID": "s", "messageID": "m1", **p} for p in parts],
        }

    export = parse_session_export(
        {
            "info": {"id": "s"},
            "messages": [
                message(
                    [
                        {"id": "t", "type": "text", "text": "hello"},
                        {"id": "r", "type": "reasoning", "text": "hmm", "time": {"start": 1_790_000_005_000}},
                        {
                            "id": "u",
                            "type": "tool",
                            "callID": "c",
                            "tool": "bash",
                            "state": {"status": "running", "input": {}, "time": {"start": 1_790_000_007_000}},
                        },
                        {
                            "id": "v",
                            "type": "tool",
                            "callID": "c2",
                            "tool": "bash",
                            "state": {"status": "pending", "input": {}},
                        },
                    ]
                )
            ],
        }
    )
    turns, _ = build_turns(export.messages, admitted=None)
    stamps = [int(t.timestamp.timestamp() * 1000) for t in turns if t.timestamp is not None]
    assert stamps == [1_790_000_000_000, 1_790_000_005_000, 1_790_000_007_000, 1_790_000_000_000]


def test_absent_or_garbled_times_yield_none_and_never_raise() -> None:
    export = parse_session_export(
        {
            "info": {"id": "s"},
            "messages": [
                {
                    "info": {"id": "m1", "sessionID": "s", "role": "user", "time": {"created": "soon"}},
                    "parts": [{"id": "t", "sessionID": "s", "messageID": "m1", "type": "text", "text": "hi"}],
                },
                {
                    "info": {"id": "m2", "sessionID": "s", "role": "user", "time": {"created": 1e30}},
                    "parts": [{"id": "t2", "sessionID": "s", "messageID": "m2", "type": "text", "text": "hi"}],
                },
            ],
        }
    )
    turns, _ = build_turns(export.messages, admitted=None)
    assert [t.timestamp for t in turns] == [None, None]


def test_a_sidechain_turn_is_stamped_through_the_transcript_source() -> None:
    source = OpenCodeTranscriptSource(_capture_exporter(), TranscriptErrorFactory(structlog.get_logger("test")))
    batch = source.turns_since(_ROOT, spawn_cwd=None, since=None)
    assert batch.available
    sidechains = [t.sidechain for t in batch.turns if t.sidechain is not None]
    assert sidechains
    assert all(turn.timestamp is not None for sidechain in sidechains for turn in sidechain.turns)
    assert all(t.timestamp is not None for t in batch.turns)


# --- descendant usage fold -------------------------------------------


def _fold(adapter: OpenCodeAdapter, generation: int, model: str | None = None) -> Any:
    sample = adapter.parse_usage(_generation(generation), "spawn", model=model)
    assert sample is not None
    return sample


def test_a_spawning_invocation_counts_root_plus_child_steps_in_its_task_windows(spawn_executor: Executor) -> None:
    exporter = _capture_exporter()
    sample = _fold(_adapter(spawn_executor, exporter), 1)
    windows = _task_windows(1)
    expected = _root_steps(1) + _child_steps_in(_CONTINUED_CHILD, [windows[_CONTINUED_CHILD]])
    assert (
        sample.input_tokens,
        sample.output_tokens,
        sample.cache_read_tokens,
        sample.cache_create_tokens,
    ) == _tokens(expected)
    assert len(_child_steps_in(_CONTINUED_CHILD, [windows[_CONTINUED_CHILD]])) > 0
    assert exporter.calls == [_CONTINUED_CHILD]


def test_a_child_continued_across_two_generations_is_counted_once(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, _capture_exporter())
    first, second = _fold(adapter, 1), _fold(adapter, 2)
    w1, w2 = _task_windows(1), _task_windows(2)
    assert w1[_CONTINUED_CHILD] != w2[_CONTINUED_CHILD]
    assert w1[_CONTINUED_CHILD][1] < w2[_CONTINUED_CHILD][0]
    total = [
        *_root_steps(1),
        *_root_steps(2),
        *_child_steps_in(_CONTINUED_CHILD, [w1[_CONTINUED_CHILD], w2[_CONTINUED_CHILD]]),
        *_child_steps_in(_OTHER_CHILD, [w2[_OTHER_CHILD]]),
    ]
    got = tuple(a + b for a, b in zip(_pair(first), _pair(second), strict=True))
    assert got == _tokens(total)
    only_second = [
        *_root_steps(2),
        *_child_steps_in(_CONTINUED_CHILD, [w2[_CONTINUED_CHILD]]),
        *_child_steps_in(_OTHER_CHILD, [w2[_OTHER_CHILD]]),
    ]
    assert _pair(second) == _tokens(only_second)


def _pair(sample: Any) -> tuple[int, int, int, int]:
    return (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens)


def test_a_child_on_another_model_is_priced_at_its_own_model(spawn_executor: Executor) -> None:
    sol = OpenCodeModelPrice(base=OpenCodeRate(input=1.0, output=2.0, cache_read=0.1, cache_write=0.5))
    terra = OpenCodeModelPrice(base=OpenCodeRate(input=10.0, output=20.0, cache_read=1.0, cache_write=5.0))

    class Catalog:
        def price_for(self, provider: str, model: str) -> OpenCodeModelPrice | None:
            return {"gpt-5.6-sol": sol, "gpt-5.6-terra": terra}.get(model)

    adapter = _adapter(spawn_executor, _capture_exporter(), price_catalog=Catalog())
    sample = _fold(adapter, 1, model="openai/gpt-5.6-sol")
    windows = _task_windows(1)

    def price(parts: list[dict[str, Any]], p: OpenCodeModelPrice) -> float:
        return sum(
            p.base.input * x["tokens"]["input"] / 1e6
            + p.base.output * (x["tokens"]["output"] + x["tokens"]["reasoning"]) / 1e6
            + p.base.cache_read * x["tokens"]["cache"]["read"] / 1e6
            + p.base.cache_write * x["tokens"]["cache"]["write"] / 1e6
            for x in parts
        )

    expected = price(_root_steps(1), sol) + price(_child_steps_in(_CONTINUED_CHILD, [windows[_CONTINUED_CHILD]]), terra)
    assert sample.estimated_cost_usd == pytest.approx(expected)


# --- synthetic sessions -------------------------------------------------------


def _step(part_id: str, session: str, message: str, tokens: int = 10, cost: float = 0) -> dict[str, Any]:
    return {
        "id": part_id,
        "sessionID": session,
        "messageID": message,
        "type": "step-finish",
        "reason": "stop",
        "cost": cost,
        "tokens": {"input": tokens, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}},
    }


def _task(
    part_id: str,
    session: str,
    message: str,
    child: str,
    start: int,
    end: int | None,
) -> dict[str, Any]:
    time: dict[str, int] = {"start": start} if end is None else {"start": start, "end": end}
    return {
        "id": part_id,
        "sessionID": session,
        "messageID": message,
        "type": "tool",
        "callID": f"call_{part_id}",
        "tool": "task",
        "state": {
            "status": "completed" if end is not None else "running",
            "input": {"subagent_type": "x"},
            "output": "ok",
            "time": time,
            "metadata": {"sessionId": child},
        },
    }


def _session(session: str, parent: str | None, messages: list[tuple[str, int, str, list[dict[str, Any]]]]) -> str:
    info: dict[str, Any] = {"id": session}
    if parent is not None:
        info["parentID"] = parent
    return json.dumps(
        {
            "info": info,
            "messages": [
                {
                    "info": {
                        "id": mid,
                        "sessionID": session,
                        "role": "assistant",
                        "providerID": "openai",
                        "modelID": model,
                        "time": {"created": created},
                    },
                    "parts": parts,
                }
                for mid, created, model, parts in messages
            ],
        }
    )


def _events(parts: list[dict[str, Any]], *, root: str = "root", stamp: int = 1_000) -> str:
    return "\n".join(
        json.dumps(
            {
                "type": "tool_use" if p["type"] == "tool" else "step_finish",
                "sessionID": root,
                "timestamp": stamp,
                "part": p,
            }
        )
        for p in parts
    )


def _synthetic() -> tuple[FakeExporter, str]:
    task = _task("t1", "root", "m0", "child", 100, 200)
    exporter = FakeExporter(
        {
            "child": _session(
                "child",
                "root",
                [
                    (
                        "c1",
                        110,
                        "gpt-5.6-terra",
                        [_step("cs1", "child", "c1", 20), _task("t2", "child", "c1", "grand", 120, 150)],
                    ),
                    ("c0", 50, "gpt-5.6-terra", [_step("cs0", "child", "c0", 900)]),  # before the window
                ],
            ),
            "grand": _session("grand", "child", [("g1", 130, "gpt-5.6-luna", [_step("gs1", "grand", "g1", 5)])]),
        }
    )
    return exporter, _events([task, _step("rs1", "root", "m0", 3)])


def test_a_grandchild_is_folded_through_its_parent_and_out_of_window_steps_are_skipped(
    spawn_executor: Executor,
) -> None:
    exporter, output = _synthetic()
    sample = _adapter(spawn_executor, exporter).parse_usage(output, "spawn")
    assert sample is not None
    assert sample.input_tokens == 3 + 20 + 5
    assert exporter.calls == ["child", "grand"]


def test_a_failing_child_export_still_records_the_root_and_logs(spawn_executor: Executor) -> None:
    exporter = FakeExporter({"child": OpenCodeExportError("gone")})
    output = _events([_task("t1", "root", "m0", "child", 100, 200), _step("rs1", "root", "m0", 3)])
    with capture_logs() as logs:
        sample = _adapter(spawn_executor, exporter).parse_usage(output, "spawn")
    assert sample is not None
    assert sample.input_tokens == 3
    assert [entry["session_id"] for entry in logs if entry["event"] == "opencode_descendant_export_unreadable"] == [
        "child"
    ]


def test_a_child_that_names_another_parent_contributes_nothing(spawn_executor: Executor) -> None:
    exporter = FakeExporter(
        {"child": _session("child", "elsewhere", [("c1", 110, "m", [_step("cs1", "child", "c1", 20)])])}
    )
    output = _events([_task("t1", "root", "m0", "child", 100, 200), _step("rs1", "root", "m0", 3)])
    sample = _adapter(spawn_executor, exporter).parse_usage(output, "spawn")
    assert sample is not None
    assert sample.input_tokens == 3


def test_a_task_that_never_ended_is_closed_at_the_latest_event_instant(spawn_executor: Executor) -> None:
    exporter = FakeExporter(
        {
            "child": _session(
                "child",
                "root",
                [
                    ("c1", 110, "m", [_step("cs1", "child", "c1", 20)]),
                    ("c2", 5_000, "m", [_step("cs2", "child", "c2", 700)]),  # after the last event
                ],
            )
        }
    )
    output = _events([_task("t1", "root", "m0", "child", 100, None), _step("rs1", "root", "m0", 3)], stamp=1_000)
    sample = _adapter(spawn_executor, exporter).parse_usage(output, "spawn")
    assert sample is not None
    assert sample.input_tokens == 3 + 20


def test_running_task_after_stdout_is_recovered_only_in_its_invocation(spawn_executor: Executor) -> None:
    root = _session(
        "root",
        None,
        [
            (
                "m0",
                1_100,
                "root-model",
                [_task("t1", "root", "m0", "child", 1_200, None)],
            )
        ],
    )
    child = _session(
        "child",
        "root",
        [
            ("c1", 1_300, "child-model", [_step("cs1", "child", "c1", 20)]),
            ("c2", 1_700, "child-model", [_step("cs2", "child", "c2", 30)]),
            ("c3", 2_300, "child-model", [_step("cs3", "child", "c3", 700)]),
        ],
    )
    exporter = FakeExporter({"root": root, "child": child})
    adapter = _adapter(spawn_executor, exporter)

    def at(ms: int) -> datetime:
        return datetime.fromtimestamp(ms / 1000, UTC)

    first = adapter.parse_usage(
        _events([_step("rs1", "root", "m0", 3)], stamp=1_000),
        "spawn",
        invocation_start=at(900),
        invocation_end=at(1_500),
    )
    assert first is not None
    assert first.input_tokens == 23
    # The same root export remains available when the next generation continues the child.
    second = adapter.parse_usage(
        _events([_task("t2", "root", "m1", "child", 1_600, 2_000), _step("rs2", "root", "m1", 4)], stamp=1_900),
        "resume",
        invocation_start=at(1_550),
        invocation_end=at(2_050),
    )
    assert second is not None
    assert second.input_tokens == 34
    assert exporter.calls == ["root", "child", "root", "child"]


def test_recovered_child_estimate_uses_its_model_and_is_all_or_nothing(spawn_executor: Executor) -> None:
    root = _session("root", None, [("m0", 1_100, "root-model", [_task("t1", "root", "m0", "child", 1_200, None)])])
    child = _session("child", "root", [("c1", 1_300, "child-model", [_step("cs1", "child", "c1", 20)])])

    class Catalog:
        def __init__(self, child_priced: bool) -> None:
            self.child_priced = child_priced

        def price_for(self, provider: str, model: str) -> OpenCodeModelPrice | None:
            if provider != "openai" or (model == "child-model" and not self.child_priced):
                return None
            rate = OpenCodeRate(input=10 if model == "child-model" else 1, output=0, cache_read=0, cache_write=0)
            return OpenCodeModelPrice(base=rate)

    output = _events([_step("rs1", "root", "m0", 3)], stamp=1_000)
    for priced in (True, False):
        adapter = _adapter(spawn_executor, FakeExporter({"root": root, "child": child}), price_catalog=Catalog(priced))
        sample = adapter.parse_usage(
            output,
            "spawn",
            model="openai/root-model",
            invocation_start=datetime.fromtimestamp(0.9, UTC),
            invocation_end=datetime.fromtimestamp(1.5, UTC),
        )
        assert sample is not None
        assert sample.input_tokens == 23
        assert sample.cost_usd is None
        if priced:
            assert sample.estimated_cost_usd == pytest.approx(203 / 1e6)
        else:
            assert sample.estimated_cost_usd is None


def test_a_cycle_between_sessions_terminates(spawn_executor: Executor) -> None:
    exporter = FakeExporter(
        {
            "child": _session(
                "child",
                "root",
                [("c1", 110, "m", [_step("cs1", "child", "c1", 20), _task("t2", "child", "c1", "root", 100, 200)])],
            ),
        }
    )
    output = _events([_task("t1", "root", "m0", "child", 100, 200), _step("rs1", "root", "m0", 3)])
    sample = _adapter(spawn_executor, exporter).parse_usage(output, "spawn")
    assert sample is not None
    assert sample.input_tokens == 23
    assert exporter.calls == ["child"]


def test_one_unpriceable_step_voids_the_estimate_and_a_missing_export_does_not(spawn_executor: Executor) -> None:
    priced = OpenCodeModelPrice(base=OpenCodeRate(input=1.0, output=1.0, cache_read=0.0, cache_write=0.0))

    class Catalog:
        def price_for(self, provider: str, model: str) -> OpenCodeModelPrice | None:
            return priced if model == "gpt-5.6-terra" else None

    exporter, output = _synthetic()
    sample = _adapter(spawn_executor, exporter, price_catalog=Catalog()).parse_usage(
        output, "spawn", model="openai/gpt-5.6-terra"
    )
    assert sample is not None
    assert sample.estimated_cost_usd is None

    exporter.scripts["grand"] = OpenCodeExportError("gone")
    sample = _adapter(spawn_executor, exporter, price_catalog=Catalog()).parse_usage(
        output, "spawn", model="openai/gpt-5.6-terra"
    )
    assert sample is not None
    assert sample.estimated_cost_usd == pytest.approx((3 + 1 + 20 + 1) / 1e6)


def test_a_child_step_with_its_own_cost_adds_into_cost_usd(spawn_executor: Executor) -> None:
    exporter = FakeExporter(
        {"child": _session("child", "root", [("c1", 110, "m", [_step("cs1", "child", "c1", 20, cost=0.5)])])}
    )
    output = _events([_task("t1", "root", "m0", "child", 100, 200), _step("rs1", "root", "m0", 3, cost=0.25)])
    sample = _adapter(spawn_executor, exporter).parse_usage(output, "spawn")
    assert sample is not None
    assert sample.cost_usd == pytest.approx(0.75)


# --- the transcript fallback path ---------------------------------------------


def _lines(output: str) -> list[str]:
    return output.splitlines()


def test_the_transcript_fallback_folds_descendants_from_root_export_lines(spawn_executor: Executor) -> None:
    exporter, _ = _synthetic()
    root_message = json.dumps(
        {
            "info": {
                "id": "m0",
                "sessionID": "root",
                "role": "assistant",
                "providerID": "openai",
                "modelID": "gpt-5.6-sol",
                "time": {"created": 90, "completed": 210},
            },
            "parts": [_task("t1", "root", "m0", "child", 100, 200), _step("rs1", "root", "m0", 3)],
        }
    )
    sample = _adapter(spawn_executor, exporter).sum_transcript_usage([root_message], "spawn")
    assert sample.input_tokens == 3 + 20 + 5
    assert sample.cost_usd is None


def test_transcript_fallback_caps_running_task_at_invocation_end(spawn_executor: Executor) -> None:
    exporter = FakeExporter(
        {
            "child": _session(
                "child",
                "root",
                [
                    ("c1", 1_300, "m", [_step("cs1", "child", "c1", 20)]),
                    ("c2", 1_700, "m", [_step("cs2", "child", "c2", 700)]),
                ],
            )
        }
    )
    line = _session("root", None, [("m0", 1_100, "m", [_task("t1", "root", "m0", "child", 1_200, None)])])
    [message] = json.loads(line)["messages"]
    sample = _adapter(spawn_executor, exporter).sum_transcript_usage(
        [json.dumps(message)],
        "spawn",
        invocation_start=datetime.fromtimestamp(0.9, UTC),
        invocation_end=datetime.fromtimestamp(1.5, UTC),
    )
    assert sample.input_tokens == 20


def test_the_transcript_fallback_reads_a_capture_export_message(spawn_executor: Executor) -> None:
    root = json.loads(_load("root_export.json"))
    lines = [json.dumps(m) for m in root["messages"] if m["info"]["time"]["created"] <= 1789384646158]
    windows = _task_windows(1)
    sample = _adapter(spawn_executor, _capture_exporter()).sum_transcript_usage(lines, "spawn")
    child = _child_steps_in(_CONTINUED_CHILD, [windows[_CONTINUED_CHILD]])
    root_steps = [
        p
        for m in root["messages"]
        if m["info"]["time"]["created"] <= 1789384646158
        for p in m["parts"]
        if p["type"] == "step-finish"
    ]
    assert _pair(sample) == _tokens([*root_steps, *child])


def test_with_no_collector_usage_is_the_root_alone(spawn_executor: Executor) -> None:
    sample = _adapter(spawn_executor, None).parse_usage(_generation(1), "spawn")
    assert sample is not None
    assert _pair(sample) == _tokens(_root_steps(1))


def test_the_fixture_files_are_not_a_corpus(tmp_path: Path) -> None:
    from blizzard.runner.harness.offline_compatibility import admitted_corpus_versions
    from blizzard.runner.harness.opencode.compatibility.probe import ADMITTED_OPENCODE_RANGE

    assert "1.18.32" not in admitted_corpus_versions("opencode", ADMITTED_OPENCODE_RANGE)

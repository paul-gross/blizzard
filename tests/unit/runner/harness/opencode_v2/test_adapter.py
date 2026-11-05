"""The OpenCode 2 adapter: session-id minting and runner metadata, the driver launch each seam operation composes, the
prompt kept off the command line, model references and variants, and the verdict, usage, child usage, limit, and
overload it reads from the driver's result."""

from __future__ import annotations

import io
import json
import stat
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from blizzard.foundation.ids import Id
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import HarnessSpawnError, HarnessVersionChangedError, WorkerPreamble
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.harness.opencode_v2.adapter import OpenCodeTwoAdapter, window_tokens
from blizzard.runner.harness.opencode_v2.client import ModelReference
from blizzard.runner.harness.opencode_v2.driver import ServiceIdentity
from blizzard.runner.harness.opencode_v2.errors import ErrorKind, TurnError
from blizzard.runner.harness.opencode_v2.request import DriveKind, DriveRequest
from blizzard.runner.harness.opencode_v2.result import Outcome, ResultWriter, Step
from blizzard.runner.harness.opencode_v2.service import ServiceUnavailableError
from blizzard.runner.harness.opencode_v2.shapes import Tokens
from blizzard.runner.harness.process_launch import LaunchedProcess
from blizzard.runner.node_steps.envelope import Envelope
from blizzard.runner.process.child_env import AllowlistedEnv, ChildEnv
from blizzard.runner.process.child_processes import StreamTarget
from tests.support.child_launch import CHILDREN, tool_env
from tests.support.runner_fakes import make_envelope

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Launcher:
    def __init__(self) -> None:
        self.argvs: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.cwds: list[str | None] = []
        self.requests: list[DriveRequest] = []

    def launch(
        self,
        argv: list[str],
        *,
        cwd: str | None,
        env: ChildEnv,
        stdout: StreamTarget,
        stderr: StreamTarget,
        defer_disarm: bool = False,
    ) -> LaunchedProcess:
        assert defer_disarm
        self.argvs.append(list(argv))
        self.envs.append(env.variables)
        self.cwds.append(cwd)
        path = Path(argv[-1])
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        self.requests.append(DriveRequest.parse(path.read_bytes()))
        return LaunchedProcess(pid=4242, pgid=4242, process_start_time="1", confirm_durable=lambda: None)


class _Keeper:
    def __init__(self, *, up: bool = True, version: str = "2.1.4") -> None:
        self.up = up
        self.version = version
        self.ensured = 0

    def answering(self) -> ServiceIdentity | None:
        return ServiceIdentity("svc-1", 100, self.version) if self.up else None

    def ensure(self) -> ServiceIdentity:
        self.ensured += 1
        if not self.up:
            raise ServiceUnavailableError("nothing answers")
        return ServiceIdentity("svc-1", 100, self.version)


def _adapter(tmp_path: Path, *, keeper: _Keeper | None = None, **kwargs: Any) -> tuple[OpenCodeTwoAdapter, _Launcher]:
    launcher = _Launcher()
    adapter = OpenCodeTwoAdapter(
        runner_id="run_1",
        worker_env=AllowlistedEnv.of(()),
        launcher=launcher,
        keeper=keeper or _Keeper(),
        versions=VersionProbe(CHILDREN, tool_env()),
        request_dir=tmp_path / "requests",
        clock=_Clock(),
        model="openai/gpt-6-luna",
        **kwargs,
    )
    return adapter, launcher


def _envelope(prompt: str = "work") -> Envelope:
    return replace(make_envelope("chunk_1", "build", node_id="node_1", choices=[]), prompt=prompt)


def _preamble(tmp_path: Path) -> WorkerPreamble:
    return WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(tmp_path))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        prompt_prefix="PREFIX",
        lease_token="tok-secret",
    )


def test_a_spawn_mints_its_session_id_and_launches_the_driver_with_the_prompt_in_a_file(tmp_path: Path) -> None:
    adapter, launcher = _adapter(tmp_path)
    envelope = _envelope("build the thing")
    handle = adapter.spawn(envelope, _preamble(tmp_path), "hint-ignored", effort="high", compaction_window="200k")
    assert not adapter.honors_session_hint()
    parsed = Id.parse(handle.session_id)
    assert parsed is not None and parsed.prefix == "ses" and parsed.minted_at == NOW
    argv = launcher.argvs[0]
    assert argv[:5] == ["blizzard", "runner", "opencode", "drive", "--request"]
    assert all("build the thing" not in arg for arg in argv)
    request = launcher.requests[0]
    assert request.kind is DriveKind.SPAWN and request.session_id == handle.session_id
    assert request.prompt == "PREFIX\n\nbuild the thing"
    assert request.model == ModelReference("openai", "gpt-6-luna", "high")
    assert request.compaction_tokens == 200_000
    assert request.metadata() == {"blizzard": {"runner": "run_1", "lease": "lease_1"}}
    assert "tok-secret" not in Path(argv[-1]).read_text()
    env = launcher.envs[0]
    assert env["BLIZZARD_SESSION_ID"] == handle.session_id and env["BLIZZARD_LEASE_TOKEN"] == "tok-secret"
    assert launcher.cwds == [str(tmp_path)]


def test_a_spawn_resuming_a_session_drives_that_session(tmp_path: Path) -> None:
    adapter, launcher = _adapter(tmp_path)
    handle = adapter.spawn(_envelope(), _preamble(tmp_path), None, resume_from="ses_existing")
    assert handle.session_id == "ses_existing" and launcher.requests[0].kind is DriveKind.RESUME


def test_a_judgement_writes_to_its_output_path_and_marks_the_elicitation(tmp_path: Path) -> None:
    adapter, launcher = _adapter(tmp_path)
    output = tmp_path / "verdict.out"
    output.write_text("a stale reply\n")
    adapter.judge(str(tmp_path), "ses_1", "judge it", str(output), preamble=_preamble(tmp_path), chunk_id="c1")
    assert launcher.requests[0].kind is DriveKind.JUDGE and launcher.requests[0].prompt == "judge it"
    assert launcher.envs[0]["BLIZZARD_ELICITATION"] == "1"
    assert output.read_text() == ""
    with pytest.raises(HarnessSpawnError):
        adapter.judge(str(tmp_path), "ses_1", "judge it", "")


def test_a_nudge_is_a_resumed_turn(tmp_path: Path) -> None:
    adapter, launcher = _adapter(tmp_path)
    handle = adapter.resume_with_message(str(tmp_path), "ses_1", "keep going", preamble=_preamble(tmp_path))
    assert handle.pgid == 4242 and launcher.requests[0].kind is DriveKind.NUDGE


def test_no_service_fails_the_launch_before_any_request_is_written(tmp_path: Path) -> None:
    keeper = _Keeper(up=False)
    adapter, launcher = _adapter(tmp_path, keeper=keeper)
    with pytest.raises(HarnessSpawnError, match="no OpenCode service"):
        adapter.spawn(_envelope(), _preamble(tmp_path), None)
    assert keeper.ensured == 1 and not launcher.argvs
    assert not (tmp_path / "requests").exists() or not list((tmp_path / "requests").iterdir())


class _Orphaner:
    def __init__(self) -> None:
        self.orphaned: list[str] = []

    def orphan(self, session_id: str) -> None:
        self.orphaned.append(session_id)


def test_the_advertised_version_is_the_services_and_the_binarys_while_none_answers(tmp_path: Path) -> None:
    keeper = _Keeper(up=False)
    binary = tmp_path / "opencode"
    binary.write_text("#!/bin/sh\necho 'opencode v2.1.3'\n")
    binary.chmod(0o755)
    adapter, _ = _adapter(tmp_path, keeper=keeper, binary=str(binary))
    assert adapter.observe_version() == "2.1.3"
    keeper.up = True
    assert adapter.observe_version() == "2.1.4"


def test_a_launch_on_a_service_whose_version_changed_is_refused_once_then_accepted(tmp_path: Path) -> None:
    keeper = _Keeper()
    adapter, launcher = _adapter(tmp_path, keeper=keeper)
    assert adapter.observe_version() == "2.1.4"
    adapter.spawn(_envelope(), _preamble(tmp_path), None)
    keeper.version = "2.2.0"
    with pytest.raises(HarnessVersionChangedError, match=r"2\.2\.0, not 2\.1\.4"):
        adapter.spawn(_envelope(), _preamble(tmp_path), None)
    assert len(launcher.requests) == 1
    adapter.spawn(_envelope(), _preamble(tmp_path), None)
    assert len(launcher.requests) == 2


def test_orphan_hands_the_session_to_the_orphaner(tmp_path: Path) -> None:
    orphaner = _Orphaner()
    adapter, launcher = _adapter(tmp_path, orphaner=orphaner)
    adapter.orphan("ses_1")
    assert orphaner.orphaned == ["ses_1"] and launcher.requests == []


def test_models_and_efforts_resolve_and_takeover_goes_through_attend(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path, model_aliases=[("tier:fast", "openai/gpt-6-mini")], effort_aliases=[("x", "max")])
    assert adapter.resolve_model(["tier:fast"]) == "openai/gpt-6-mini"
    assert adapter.resolve_model_strict(["not-a-reference"]) is None
    assert adapter.resolve_effort("x") == "max" and adapter.resolve_effort("weird") is None
    assert adapter.resolve_compaction_window("auto") == "auto" and adapter.resolve_compaction_window("x") is None
    assert window_tokens("auto") is None and window_tokens("150000") == 150_000
    command = adapter.resume_command("/w", "ses_1", model="openai/gpt-6-luna", effort="high")
    assert command == "cd /w && blizzard runner opencode attend ses_1 --model 'openai/gpt-6-luna#high'"
    attended = adapter.resume_command("/my work", "ses_1", attended=True)
    assert attended == "cd '/my work' && blizzard runner opencode attend ses_1 --model openai/gpt-6-luna"


def test_the_takeover_command_names_a_binary_other_than_opencode(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path, binary="/opt/oc 2/opencode")
    command = adapter.resume_command("/w", "ses_1")
    assert command.endswith("attend ses_1 --model openai/gpt-6-luna --opencode '/opt/oc 2/opencode'")


def _output(outcome: Outcome, text: str, error: TurnError | None = None) -> str:
    out = io.StringIO()
    tokens = Tokens(input=100, output=10, reasoning=5, cache_read=50, cache_write=2)
    steps = [
        Step("ses_root", None, "msg_1", "openai/gpt-6-luna", tokens, 0.5),
        Step("ses_child", "ses_root", "msg_2", "openai/gpt-6-mini", tokens, 0.0),
    ]
    ResultWriter(out, "ses_root").finish(outcome, text, steps, error)
    return out.getvalue()


def test_the_verdict_and_assessment_come_from_the_final_root_text(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path)
    output = _output(Outcome.SUCCEEDED, "thinking <Choice>approve</Choice> looks good")
    assert adapter.parse_verdict(output) == "approve"
    assert adapter.parse_assessment(output) == "looks good"
    assert adapter.has_usable_output(output)
    truncated = output.rsplit("\n", 2)[0]
    assert not adapter.has_usable_output(truncated) and adapter.parse_verdict(truncated) is None


def test_usage_sums_the_root_and_every_descendant(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path)
    sample = adapter.parse_usage(_output(Outcome.SUCCEEDED, "ok"), "spawn")
    assert sample is not None
    assert (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens) == (
        200,
        30,
        100,
        4,
    )
    assert sample.cost_usd == 0.5 and sample.model == "openai/gpt-6-luna"
    assert not adapter.needs_usage_transcript("")


def test_a_usage_limit_carries_its_reset_and_an_overload_classifies(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path)
    limited = _output(Outcome.FAILED, "", TurnError(ErrorKind.USAGE_LIMIT, "limit", retry_after_ms=3_600_000))
    limit = adapter.classify_usage_limit(limited, [], NOW)
    assert limit is not None and limit.resets_at == NOW + timedelta(hours=1)
    assert adapter.classify_provider_overload(limited, []) is None
    overloaded = _output(Outcome.FAILED, "", TurnError(ErrorKind.OVERLOAD, "busy", status=529))
    overload = adapter.classify_provider_overload(overloaded, [])
    assert overload is not None and overload.detail == "busy"
    assert adapter.classify_usage_limit(overloaded, [], NOW) is None
    assert adapter.parse_assessment(overloaded) == "busy"


def test_a_spawn_without_an_environment_is_refused(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path)
    preamble = WorkerPreamble(environments=[], lease_id="l", local_api_url="http://127.0.0.1:1")
    with pytest.raises(HarnessSpawnError):
        adapter.spawn(_envelope(), preamble, None)


def test_the_autonomy_rides_the_request(tmp_path: Path) -> None:
    adapter, launcher = _adapter(tmp_path, autonomy=Autonomy.Normal)
    adapter.spawn(_envelope(), _preamble(tmp_path), None)
    assert launcher.requests[0].autonomy is Autonomy.Normal


def _record(session: str, parent: str | None, message: dict[str, Any]) -> str:
    return json.dumps({"session": session, "parent": parent, "message": message})


def _step(message_id: str, model: str, *, cost: float = 0.0, completed: bool = True) -> dict[str, Any]:
    provider, name = model.split("/")
    step: dict[str, Any] = {
        "type": "assistant",
        "id": message_id,
        "time": {"created": 1, **({"completed": 2} if completed else {})},
        "content": [],
        "model": {"providerID": provider, "id": name, "variant": "high"},
    }
    if completed:
        step |= {"tokens": {"input": 10, "output": 2, "reasoning": 1, "cache": {"read": 5, "write": 1}}, "cost": cost}
    return step


def test_a_killed_turns_usage_is_recovered_from_the_root_and_every_descendant_in_its_records(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path)
    lines = [
        _record("ses_r", None, {"type": "user", "id": "msg_u", "time": {"created": 0}, "text": "go"}),
        _record("ses_r", None, _step("msg_1", "openai/gpt-6-luna")),
        _record("ses_c", "ses_r", _step("msg_c", "openai/gpt-6-mini")),
        _record("ses_r", None, _step("msg_2", "openai/gpt-6-luna", completed=False)),
        "not json",
    ]
    sample = adapter.sum_transcript_usage(lines, "spawn")
    assert (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens) == (
        20,
        6,
        10,
        2,
    )
    assert sample.cost_usd is None and sample.model == "openai/gpt-6-luna"
    assert adapter.observed_model(lines) == "openai/gpt-6-luna"


def test_a_compactions_usage_is_recovered_with_the_steps(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path)
    compaction = _step("msg_k", "openai/gpt-6-luna", cost=0.5) | {"type": "compaction", "summary": "so far"}
    lines = [_record("ses_r", None, _step("msg_1", "openai/gpt-6-luna")), _record("ses_r", None, compaction)]
    sample = adapter.sum_transcript_usage(lines, "spawn")
    assert (sample.input_tokens, sample.output_tokens, sample.cost_usd) == (20, 6, 0.5)


def test_a_reported_cost_in_the_records_is_summed(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path)
    lines = [_record("ses_r", None, _step("msg_1", "openai/gpt-6-luna", cost=0.25))] * 2
    assert adapter.sum_transcript_usage(lines, "spawn").cost_usd == 0.5
    assert adapter.observed_model([]) is None

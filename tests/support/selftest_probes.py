"""Helpers for the runner selftest tests, shared with other test modules."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

from blizzard.runner.harness.adapter import ResumeHandle, WorkerHandle, WorkerPreamble
from blizzard.runner.harness.overload import ProviderOverload
from blizzard.runner.harness.transcript import IHarnessTranscriptSource, NullTranscriptSource
from blizzard.runner.harness.usage import UsageKind, UsageLimit, UsageSample


class FixedPidAdapter:
    """Spawns and resumes as fast, well-behaved subprocess mechanics honoring fixed
    pids — isolates the automated-resume reap behavior from real process spawning."""

    def __init__(self, spawn_pid: int, resume_pid: int) -> None:
        self.spawn_pid = spawn_pid
        self.resume_pid = resume_pid

    def observe_version(self) -> str | None:
        return "test"

    def spawn(
        self,
        envelope: object,
        preamble: object,
        session_hint: str | None,
        resume_from: str | None = None,
        *,
        model: str | None = None,
        effort: str | None = None,
        compaction_window: str | None = None,
    ) -> WorkerHandle:
        return WorkerHandle(
            session_id=session_hint or "sid", pid=self.spawn_pid, process_start_time="spawn-t", pgid=self.spawn_pid
        )

    def honors_session_hint(self) -> bool:
        return True

    def resume_with_message(
        self,
        session_cwd: str,
        session_id: str,
        message: str,
        stdout_path: str = "",
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        model: str | None = None,
        effort: str | None = None,
        compaction_window: str | None = None,
    ) -> ResumeHandle:
        return ResumeHandle(pid=self.resume_pid, pgid=self.resume_pid, process_start_time="resume-t")

    def resume_command(
        self,
        session_cwd: str,
        session_id: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        attended: bool = False,
    ) -> str:
        return f"cd {session_cwd} && fake --resume {session_id}"

    def orphan(self, session_id: str) -> None:
        del session_id

    def reconcile(self, session_ids: Sequence[str]) -> int:
        del session_ids
        return 0

    def identity_env(self, preamble: WorkerPreamble, chunk_id: str, session_id: str) -> dict[str, str]:
        return {"BLIZZARD_SESSION_ID": session_id, "BLIZZARD_CHUNK_ID": chunk_id}

    def judge(
        self,
        session_cwd: str,
        session_id: str,
        judgement_prompt: str,
        output_path: str,
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        effort: str | None = None,
        model: str | None = None,
        compaction_window: str | None = None,
    ) -> WorkerHandle:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"result": "<Choice>pass</Choice>"}))
        return WorkerHandle(
            session_id=session_id, pid=self.spawn_pid, process_start_time="judge-t", pgid=self.spawn_pid
        )

    def parse_verdict(self, output: str) -> str | None:
        return "pass" if "<Choice>pass</Choice>" in output else None

    def parse_assessment(self, output: str) -> str:
        return ""

    def has_usable_output(self, output: str) -> bool:
        return bool(output)

    def resolve_model(self, preferences: Sequence[str]) -> str:
        return "fake-model"

    def resolve_model_strict(self, preferences: Sequence[str]) -> str | None:
        return "fake-model" if preferences else None

    def resolve_effort(self, value: str | None) -> str | None:
        return value

    def resolve_compaction_window(self, value: str | None) -> str | None:
        return value

    def resolvable_tier_ids(self) -> tuple[str, ...]:
        return ()

    def supported_models(self) -> tuple[str, ...]:
        return ()

    def needs_usage_transcript(self, output: str, *, model: str | None = None) -> bool:
        return False

    def parse_usage(
        self,
        output: str,
        kind: UsageKind,
        *,
        model: str | None = None,
        transcript_lines: Sequence[str] = (),
        invocation_start: datetime | None = None,
        invocation_end: datetime | None = None,
    ) -> UsageSample | None:
        return None

    def observed_model(self, lines: Sequence[str]) -> str | None:
        return None

    def sum_transcript_usage(
        self,
        lines: Sequence[str],
        kind: UsageKind,
        *,
        model: str | None = None,
        observed: str | None = None,
        invocation_start: datetime | None = None,
        invocation_end: datetime | None = None,
    ) -> UsageSample:
        return UsageSample(
            kind=kind,
            model="",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_create_tokens=0,
            cost_usd=None,
        )

    def classify_usage_limit(self, output: str, lines: Sequence[str], now: datetime) -> UsageLimit | None:
        return None

    def classify_provider_overload(self, output: str, lines: Sequence[str]) -> ProviderOverload | None:
        return None

    def transcript_source(self) -> IHarnessTranscriptSource:
        return NullTranscriptSource()


class RecordingProcessProbe:
    """Reports every pid already exited (so the poll loops resolve instantly) while
    recording every ``kill``/``start_time`` call, so a test can assert the resume
    check actually reaped its pid."""

    def __init__(self) -> None:
        self.killed: list[int] = []

    def is_alive(self, pid: int, process_start_time: str) -> bool:
        return False

    def start_time(self, pid: int) -> str | None:
        return "t"

    def group_alive(self, pgid: int) -> bool:
        return False

    def kill(self, pid: int) -> None:
        self.killed.append(pid)

    def kill_group(self, pgid: int) -> None:
        return None

    def interrupt_group(self, pgid: int) -> None:
        return None

"""The selftest's seven checks — deterministic orchestration (``bzh:deterministic-shell``)
over the harness and scratch-git seams (``bzh:pluggable-seams``), issue #54.

Every op runs against one throwaway scratch repo the ``IScratchGit`` seam mints and
tears down — no chunk, lease, environment binding, or hub call is ever on this path.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import (
    DEFAULT_IDENTITY_AWAIT_TIMEOUT_SECONDS,
    IHarnessAdapter,
    WorkerHandle,
    WorkerPreamble,
)
from blizzard.runner.harness.transcript import NullTranscriptSource
from blizzard.runner.loop.elicitation_files import ElicitationFiles
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.selftest.model import (
    AUTOMATED_RESUME,
    END_TO_END_EDIT_COMMIT,
    RESUME_COMMAND,
    SPAWN_SESSION_ID,
    TRANSCRIPT_READABILITY,
    USAGE_PARSING,
    VERDICT_ELICITATION,
    SelfTestCheck,
)
from blizzard.runner.selftest.scratch_git import IScratchGit
from blizzard.wire.envelope import EnvelopeChoice, NodeConfig, NodeEnvelope

# The exit-is-done poll budget: bounded so a hung/broken adapter fails the check
# loudly rather than wedging the canary forever.
_EXIT_TIMEOUT_SECONDS = 30.0
_EXIT_POLL_INTERVAL_SECONDS = 0.05

# The automated-resume reap budget: bounded so a probe that never confirms the kill
# took cannot itself wedge the canary — a reap timeout still reports the check's result.
_REAP_TIMEOUT_SECONDS = 5.0

_TRIVIAL_TASK_PROMPT = (
    "This is blizzard's runner selftest, the adapter-drift canary. Create a file named "
    "SELFTEST.txt containing the single line 'ok', then run `git add SELFTEST.txt` and "
    '`git commit -m "selftest: trivial edit"`. Do nothing else and end your turn.'
)
_JUDGEMENT_PROMPT = (
    "Assess whether the selftest task committed SELFTEST.txt. Reply with "
    "<Choice>pass</Choice> if it did, else <Choice>fail</Choice>."
)
_RESUME_MESSAGE = "selftest: automated follow-up resume — no action needed, just acknowledge."


@dataclass(frozen=True)
class Worker:
    """A spawned selftest process, bounded on both waits so neither can wedge the canary."""

    process: IProcessProbe
    pid: int

    def wait_for_exit(self, start_time: str) -> bool:
        deadline = time.monotonic() + _EXIT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if not self.process.is_alive(self.pid, start_time):
                return True
            time.sleep(_EXIT_POLL_INTERVAL_SECONDS)
        return not self.process.is_alive(self.pid, start_time)

    def reap(self) -> None:
        start_time = self.process.start_time(self.pid)
        self.process.kill(self.pid)
        if start_time is None:  # already gone
            return
        deadline = time.monotonic() + _REAP_TIMEOUT_SECONDS
        while time.monotonic() < deadline and self.process.is_alive(self.pid, start_time):
            time.sleep(_EXIT_POLL_INTERVAL_SECONDS)


@dataclass(frozen=True)
class Scratch:
    """The throwaway repo a run is performed against, the seams its checks drive it
    through, and the session id they drive it under."""

    adapter: IHarnessAdapter
    scratch_git: IScratchGit
    process: IProcessProbe
    workdir: str
    session_id: str


@dataclass(frozen=True)
class Check:
    """One adapter-drift check, reported as exactly one pass/fail result."""

    scratch: Scratch

    def run(self) -> SelfTestCheck:
        raise NotImplementedError


@dataclass(frozen=True)
class Spawn:
    """The gate check: it alone yields the handle the rest are driven from, so it is built
    rather than run, and a ``None`` handle skips them."""

    result: SelfTestCheck
    handle: WorkerHandle | None

    @classmethod
    def of(cls, scratch: Scratch) -> Spawn:
        try:
            pending = scratch.adapter.spawn(
                cls._envelope(), cls._preamble(scratch.workdir), session_hint=scratch.session_id
            )
            pending.confirm_durable()  # F1: no durable record here to threaten — disarm now
            handle = pending.await_identity(DEFAULT_IDENTITY_AWAIT_TIMEOUT_SECONDS)
        except Exception as exc:  # the adapter is untrusted external-CLI surface
            return cls(SelfTestCheck(SPAWN_SESSION_ID, False, f"spawn raised: {exc}"), None)
        # The harness-neutral claim (D1/D2): non-empty and authoritative. Hint-equality is
        # demanded only where the adapter declares it honors the hint (Claude Code does).
        if not handle.session_id:
            detail = "spawn returned an empty session id — never authoritative"
            return cls(SelfTestCheck(SPAWN_SESSION_ID, False, detail), handle)
        if scratch.adapter.honors_session_hint() and handle.session_id != scratch.session_id:
            detail = f"expected the pre-assigned session id {scratch.session_id!r}, got {handle.session_id!r}"
            return cls(SelfTestCheck(SPAWN_SESSION_ID, False, detail), handle)
        if not Worker(scratch.process, handle.pid).wait_for_exit(handle.process_start_time):
            detail = f"worker pid {handle.pid} did not exit within {_EXIT_TIMEOUT_SECONDS}s (exit-is-done undetected)"
            return cls(SelfTestCheck(SPAWN_SESSION_ID, False, detail), handle)
        detail = f"spawned pid {handle.pid} honoring session id {handle.session_id!r}; exit-is-done detected"
        return cls(SelfTestCheck(SPAWN_SESSION_ID, True, detail), handle)

    @staticmethod
    def _preamble(workdir: str) -> WorkerPreamble:
        return WorkerPreamble(
            environments=[AcquiredEnvironment(environment_id="selftest", workdir=workdir)],
            lease_id="selftest",
            local_api_url="",
            # Never the bare `""` default: OpenCode's spawn needs a real path to learn its session id from.
            stdout_path=os.path.join(workdir, ".selftest-spawn-stdout"),
        )

    @staticmethod
    def _envelope() -> NodeEnvelope:
        node = NodeConfig(
            node_id="nd_selftest",
            node_name="selftest",
            executor=Executor.RUNNER,
            session=SessionMode.FRESH,
            judged_by=JudgedBy.WORKER,
            retries_max=0,
            produces=[],
            choices=[EnvelopeChoice(name="pass", description="the trivial task succeeded")],
        )
        return NodeEnvelope(
            chunk_id="ch_selftest",
            graph_id="gr_selftest",
            epoch=1,
            node=node,
            prompt=_TRIVIAL_TASK_PROMPT,
            judgement_prompt=None,
        )


class Commit(Check):
    def run(self) -> SelfTestCheck:
        count = self.scratch.scratch_git.commit_count(self.scratch.workdir)
        if count < 2:  # the baseline commit plus the worker's own edit
            detail = f"only {count} commit(s) in the scratch repo — no edit landed"
            return SelfTestCheck(END_TO_END_EDIT_COMMIT, False, detail)
        return SelfTestCheck(END_TO_END_EDIT_COMMIT, True, f"{count - 1} new commit(s) landed in the scratch repo")


class Judge(Check):
    def run(self) -> SelfTestCheck:
        scratch = self.scratch
        output_path = os.path.join(scratch.workdir, ".selftest-judge-output")
        try:
            handle = scratch.adapter.judge(scratch.workdir, scratch.session_id, _JUDGEMENT_PROMPT, output_path)
        except Exception as exc:
            return SelfTestCheck(VERDICT_ELICITATION, False, f"judge raised: {exc}")
        handle.confirm_durable()  # F1: no durable record here to threaten — disarm now
        # The detached launch/collect shape (blizzard#443): the canary waits out the same
        # bounded poll `end_to_end_edit_commit` uses, then reads the reply back itself.
        if not Worker(scratch.process, handle.pid).wait_for_exit(handle.process_start_time):
            return SelfTestCheck(VERDICT_ELICITATION, False, "judgement process never exited")
        output = ElicitationFiles(root="").read(output_path)
        choice = scratch.adapter.parse_verdict(output)
        if choice is None:
            return SelfTestCheck(VERDICT_ELICITATION, False, "judgement resume produced no parseable <Choice>")
        return SelfTestCheck(VERDICT_ELICITATION, True, f"parsed verdict {choice!r}")


class Resume(Check):
    def run(self) -> SelfTestCheck:
        scratch = self.scratch
        try:
            # Never the bare `""` default: it would inherit the daemon's own stdout.
            stdout_path = os.path.join(scratch.workdir, ".selftest-resume-stdout")
            resumed = scratch.adapter.resume_with_message(
                scratch.workdir, scratch.session_id, _RESUME_MESSAGE, stdout_path=stdout_path
            )
        except Exception as exc:
            return SelfTestCheck(AUTOMATED_RESUME, False, f"resume_with_message raised: {exc}")
        resumed.confirm_durable()  # F1: no durable record here to threaten — disarm now
        if resumed.pid <= 0:
            return SelfTestCheck(
                AUTOMATED_RESUME, False, f"resume_with_message returned a non-positive pid ({resumed.pid})"
            )
        # Reaped here so no live process outlives the scratch dir it is cwd'd into
        # (tests/test_runner_selftest.py).
        Worker(scratch.process, resumed.pid).reap()
        return SelfTestCheck(AUTOMATED_RESUME, True, f"resumed session {scratch.session_id!r} as pid {resumed.pid}")


class ResumeCommand(Check):
    def run(self) -> SelfTestCheck:
        scratch = self.scratch
        try:
            command = scratch.adapter.resume_command(scratch.workdir, scratch.session_id)
        except Exception as exc:
            return SelfTestCheck(RESUME_COMMAND, False, f"resume_command raised: {exc}")
        if not command or scratch.session_id not in command or scratch.workdir not in command:
            return SelfTestCheck(RESUME_COMMAND, False, f"resume command missing session/workdir: {command!r}")
        return SelfTestCheck(RESUME_COMMAND, True, command)


class UsageParsing(Check):
    """Whether ``parse_usage`` can be handed the resume check's captured stdout without
    raising (blizzard#438). A missing usage envelope is a legitimate answer here (a canary
    harness may never emit real provider usage), so this only asserts the parse path stays
    exception-free — not that a sample was actually found."""

    def run(self) -> SelfTestCheck:
        scratch = self.scratch
        stdout_path = os.path.join(scratch.workdir, ".selftest-resume-stdout")
        try:
            output = Path(stdout_path).read_text(encoding="utf-8") if os.path.exists(stdout_path) else ""
        except OSError as exc:
            return SelfTestCheck(USAGE_PARSING, False, f"could not read the resume check's own stdout: {exc}")
        try:
            sample = scratch.adapter.parse_usage(output, "resume", model=None)
        except Exception as exc:
            return SelfTestCheck(USAGE_PARSING, False, f"parse_usage raised: {exc}")
        detail = (
            f"parsed a usage sample ({sample.input_tokens} in / {sample.output_tokens} out)"
            if sample is not None
            else "no usage envelope in the resume output — parse_usage's own documented answer for one, not a fault"
        )
        return SelfTestCheck(USAGE_PARSING, True, detail)


class TranscriptReadability(Check):
    """Whether the adapter's transcript source can be queried without raising, only when it
    claims one at all — a :class:`~blizzard.runner.harness.transcript.NullTranscriptSource`
    binding passes vacuously. A canary session may leave no real transcript behind, so an
    empty or absent read passes too; only an actual exception fails this check."""

    def run(self) -> SelfTestCheck:
        scratch = self.scratch
        source = scratch.adapter.transcript_source()
        if isinstance(source, NullTranscriptSource):
            return SelfTestCheck(TRANSCRIPT_READABILITY, True, "adapter declares no transcript source to check")
        try:
            lines = source.read_raw_lines(scratch.session_id, spawn_cwd=scratch.workdir)
            position = source.tail_position(scratch.session_id, spawn_cwd=scratch.workdir)
        except Exception as exc:
            return SelfTestCheck(TRANSCRIPT_READABILITY, False, f"transcript source raised: {exc}")
        detail = f"read {len(lines)} raw line(s); tail position {position!r}"
        return SelfTestCheck(TRANSCRIPT_READABILITY, True, detail)


@dataclass(frozen=True)
class SelfTest:
    """The seven adapter-drift checks against a single throwaway scratch repo."""

    adapter: IHarnessAdapter
    scratch_git: IScratchGit
    process: IProcessProbe

    def run(self) -> list[SelfTestCheck]:
        with self.scratch_git.new_scratch_repo() as repo:
            scratch = Scratch(
                adapter=self.adapter,
                scratch_git=self.scratch_git,
                process=self.process,
                workdir=repo.workdir,
                session_id=f"selftest-{uuid.uuid4().hex[:12]}",
            )
            spawn = Spawn.of(scratch)
            checks = [spawn.result]
            if spawn.handle is None:
                skipped = "skipped — the spawn/session-id check failed first"
                checks.append(SelfTestCheck(END_TO_END_EDIT_COMMIT, False, skipped))
                checks.append(SelfTestCheck(VERDICT_ELICITATION, False, skipped))
                checks.append(SelfTestCheck(AUTOMATED_RESUME, False, skipped))
                checks.append(SelfTestCheck(RESUME_COMMAND, False, skipped))
                checks.append(SelfTestCheck(USAGE_PARSING, False, skipped))
                checks.append(SelfTestCheck(TRANSCRIPT_READABILITY, False, skipped))
                return checks

            # The id the adapter actually returned, which a failed gate check may leave
            # differing from the pre-assigned one.
            spawned = replace(scratch, session_id=spawn.handle.session_id)
            checks.append(Commit(spawned).run())
            checks.append(Judge(spawned).run())
            checks.append(Resume(spawned).run())
            checks.append(ResumeCommand(spawned).run())
            checks.append(UsageParsing(spawned).run())
            checks.append(TranscriptReadability(spawned).run())
            return checks

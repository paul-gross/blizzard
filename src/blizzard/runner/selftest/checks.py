"""The selftest's five checks — deterministic orchestration (``bzh:deterministic-shell``)
over the harness and scratch-git seams (``bzh:pluggable-seams``), issue #54:

1. spawn with a pre-assigned session id and exit-is-done detection
2. a trivial end-to-end task — the worker must actually edit and commit
3. verdict elicitation — a judgement resume that yields a parseable ``<Choice>``
4. an automated follow-up resume into the same session
5. ``resume_command`` composition (string sanity, not an interactive exec)

Every op runs against one throwaway scratch repo the ``IScratchGit`` seam mints and
tears down — no chunk, lease, environment binding, or hub call is ever on this path.
"""

from __future__ import annotations

import time
import uuid
from typing import Protocol

from blizzard.hub.domain.graph import Executor, JudgedBy, SessionMode
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import IHarnessAdapter, WorkerHandle, WorkerPreamble
from blizzard.runner.selftest.model import (
    AUTOMATED_RESUME,
    END_TO_END_EDIT_COMMIT,
    RESUME_COMMAND,
    SPAWN_SESSION_ID,
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
# took cannot itself wedge the canary — the check still reports its own result even
# if reaping times out (best-effort, per the adapter contract's "kill first").
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


class IProcessProbe(Protocol):
    """The process-liveness + best-effort-kill reads the spawn and resume checks need
    (``bzh:dependency-inversion``).

    Narrower than the loop's full seam (``runner/loop/process.py``), so this module
    owns its own Protocol rather than importing across the loop boundary — mirrors
    ``runner/domain/leases.py``'s own copy of the same narrowing. The real
    ``LinuxProcessProbe`` and any test fake satisfy it structurally.
    """

    def is_alive(self, pid: int, process_start_time: str) -> bool: ...

    def start_time(self, pid: int) -> str | None: ...

    def kill(self, pid: int) -> None: ...


def run_selftest_checks(
    adapter: IHarnessAdapter, scratch_git: IScratchGit, process: IProcessProbe
) -> list[SelfTestCheck]:
    """Run the five adapter-drift checks against a single throwaway scratch repo."""
    with scratch_git.new_scratch_repo() as repo:
        session_id = f"selftest-{uuid.uuid4().hex[:12]}"
        preamble = WorkerPreamble(
            environments=[AcquiredEnvironment(environment_id="selftest", workdir=repo.workdir)],
            lease_id="selftest",
            local_api_url="",
        )

        spawn_check, handle = _check_spawn(adapter, preamble, session_id, process)
        checks = [spawn_check]
        if handle is None:
            skipped = "skipped — the spawn/session-id check failed first"
            checks.append(SelfTestCheck(END_TO_END_EDIT_COMMIT, False, skipped))
            checks.append(SelfTestCheck(VERDICT_ELICITATION, False, skipped))
            checks.append(SelfTestCheck(AUTOMATED_RESUME, False, skipped))
            checks.append(SelfTestCheck(RESUME_COMMAND, False, skipped))
            return checks

        checks.append(_check_commit(scratch_git, repo.workdir))
        judge_check, _output = _check_judge(adapter, repo.workdir, handle.session_id)
        checks.append(judge_check)
        checks.append(_check_resume(adapter, repo.workdir, handle.session_id, process))
        checks.append(_check_resume_command(adapter, repo.workdir, handle.session_id))
        return checks


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


def _check_spawn(
    adapter: IHarnessAdapter, preamble: WorkerPreamble, session_id: str, process: IProcessProbe
) -> tuple[SelfTestCheck, WorkerHandle | None]:
    try:
        handle = adapter.spawn(_envelope(), preamble, session_hint=session_id)
    except Exception as exc:  # the adapter is untrusted external-CLI surface
        return SelfTestCheck(SPAWN_SESSION_ID, False, f"spawn raised: {exc}"), None
    if handle.session_id != session_id:
        detail = f"expected the pre-assigned session id {session_id!r}, got {handle.session_id!r}"
        return SelfTestCheck(SPAWN_SESSION_ID, False, detail), handle
    if not _wait_for_exit(process, handle.pid, handle.process_start_time):
        detail = f"worker pid {handle.pid} did not exit within {_EXIT_TIMEOUT_SECONDS}s (exit-is-done undetected)"
        return SelfTestCheck(SPAWN_SESSION_ID, False, detail), handle
    detail = f"spawned pid {handle.pid} honoring session id {handle.session_id!r}; exit-is-done detected"
    return SelfTestCheck(SPAWN_SESSION_ID, True, detail), handle


def _wait_for_exit(process: IProcessProbe, pid: int, start_time: str) -> bool:
    deadline = time.monotonic() + _EXIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not process.is_alive(pid, start_time):
            return True
        time.sleep(_EXIT_POLL_INTERVAL_SECONDS)
    return not process.is_alive(pid, start_time)


def _check_commit(scratch_git: IScratchGit, workdir: str) -> SelfTestCheck:
    count = scratch_git.commit_count(workdir)
    if count < 2:  # the baseline commit plus the worker's own edit
        detail = f"only {count} commit(s) in the scratch repo — no edit landed"
        return SelfTestCheck(END_TO_END_EDIT_COMMIT, False, detail)
    return SelfTestCheck(END_TO_END_EDIT_COMMIT, True, f"{count - 1} new commit(s) landed in the scratch repo")


def _check_judge(adapter: IHarnessAdapter, workdir: str, session_id: str) -> tuple[SelfTestCheck, str]:
    try:
        output = adapter.judge(workdir, session_id, _JUDGEMENT_PROMPT)
    except Exception as exc:
        return SelfTestCheck(VERDICT_ELICITATION, False, f"judge raised: {exc}"), ""
    choice = adapter.parse_verdict(output)
    if choice is None:
        return SelfTestCheck(VERDICT_ELICITATION, False, "judgement resume produced no parseable <Choice>"), output
    return SelfTestCheck(VERDICT_ELICITATION, True, f"parsed verdict {choice!r}"), output


def _check_resume(adapter: IHarnessAdapter, workdir: str, session_id: str, process: IProcessProbe) -> SelfTestCheck:
    try:
        pid = adapter.resume_with_message(workdir, session_id, _RESUME_MESSAGE)
    except Exception as exc:
        return SelfTestCheck(AUTOMATED_RESUME, False, f"resume_with_message raised: {exc}")
    if pid <= 0:
        return SelfTestCheck(AUTOMATED_RESUME, False, f"resume_with_message returned a non-positive pid ({pid})")
    # `resume_with_message` is fire-and-forget (the adapter contract: "never run
    # against a live process — kill first"), and `run_selftest_checks` tears down the
    # scratch repo this pid's cwd is in as soon as the check suite finishes. Reap it
    # here so no live process outlives its scratch dir.
    _reap(process, pid)
    return SelfTestCheck(AUTOMATED_RESUME, True, f"resumed session {session_id!r} as pid {pid}")


def _reap(process: IProcessProbe, pid: int) -> None:
    """Kill the resumed process and wait (bounded) for it to actually exit."""
    start_time = process.start_time(pid)
    process.kill(pid)
    if start_time is None:  # already gone
        return
    deadline = time.monotonic() + _REAP_TIMEOUT_SECONDS
    while time.monotonic() < deadline and process.is_alive(pid, start_time):
        time.sleep(_EXIT_POLL_INTERVAL_SECONDS)


def _check_resume_command(adapter: IHarnessAdapter, workdir: str, session_id: str) -> SelfTestCheck:
    try:
        command = adapter.resume_command(workdir, session_id)
    except Exception as exc:
        return SelfTestCheck(RESUME_COMMAND, False, f"resume_command raised: {exc}")
    if not command or session_id not in command or workdir not in command:
        return SelfTestCheck(RESUME_COMMAND, False, f"resume command missing session/workdir: {command!r}")
    return SelfTestCheck(RESUME_COMMAND, True, command)

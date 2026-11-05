"""The OpenCode adapter — command composition (unit) and a real subprocess (component).

Unit tests drive command building and model/effort/compaction resolution against a
monkeypatched ``subprocess.Popen``; component tests launch a fake ``opencode`` binary
(``tests/support/opencode_binary.py``)."""

from __future__ import annotations

import json
from concurrent.futures import Executor
from pathlib import Path

import pytest

from blizzard.runner.harness.adapter import WorkerIdentityError
from blizzard.runner.harness.opencode.adapter import (
    MAX_IDENTITY_PREAMBLE_LINES,
    PendingOpenCodeIdentity,
)
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from tests.support.child_launch import wait_for_exit
from tests.support.opencode_adapter import adapter as _adapter
from tests.support.opencode_adapter import preamble as _preamble
from tests.support.opencode_binary import worker_binary
from tests.support.runner_fakes import FakeProbe, make_envelope


def test_real_child_process_receives_effective_bundle_on_every_launch(tmp_path: Path, spawn_executor: Executor) -> None:
    capture = tmp_path / "launches.jsonl"
    binary = worker_binary(tmp_path, capture_path=capture)
    effective = tmp_path / "effective"
    effective.mkdir()
    config = effective / "opencode.json"
    config.write_text('{"permission":{"question":"deny"},"plugin":[]}')
    workdir = tmp_path / "project"
    workdir.mkdir()
    adapter = _adapter(
        spawn_executor,
        binary=binary,
        process=LinuxProcessProbe(),
        worker_config_path=str(config),
        effective_config_dir=str(effective),
    )
    preamble = _preamble(str(workdir), stdout_path=str(tmp_path / "fresh.jsonl"))
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    fresh = adapter.spawn(envelope, preamble, None)
    fresh.confirm_durable()
    fresh_handle = fresh.await_identity(5)
    wait_for_exit(fresh_handle.pid)
    resumed = adapter.spawn(envelope, preamble, None, resume_from=fresh_handle.session_id)
    resumed.confirm_durable()
    wait_for_exit(resumed.pid)
    nudge = adapter.resume_with_message(str(workdir), fresh_handle.session_id, "continue")
    nudge.confirm_durable()
    wait_for_exit(nudge.pid)
    judge = adapter.judge(str(workdir), fresh_handle.session_id, "assess", str(tmp_path / "judge.jsonl"))
    judge.confirm_durable()
    wait_for_exit(judge.pid)

    recorded = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(recorded) == 4
    for launch in recorded:
        assert launch["config"] == str(config)
        assert launch["content"] == config.read_text()
        assert launch["directory"] == str(effective)


# --------------------------------------------------------------------------- #
# The one command builder: every non-interactive kind carries `--format json`.


def test_fresh_spawn_reads_the_minted_session_id_from_stdout(tmp_path: Path, spawn_executor: Executor) -> None:
    binary = worker_binary(tmp_path, minted_session_id="ses_minted_abc")
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(spawn_executor, binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    pending = adapter.spawn(envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint")
    pending.confirm_durable()  # real component tests stand in for `Spawner.spawn`'s own call
    handle = pending.await_identity(5.0)

    assert handle.session_id == "ses_minted_abc"
    assert handle.pid > 0
    assert handle.process_start_time
    wait_for_exit(handle.pid)


def test_fresh_spawn_never_passes_the_hint_as_session_id(tmp_path: Path, spawn_executor: Executor) -> None:
    binary = worker_binary(tmp_path, minted_session_id="ses_self_assigned")
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(spawn_executor, binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    pending = adapter.spawn(
        envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint-never-honored"
    )
    pending.confirm_durable()  # real component tests stand in for `Spawner.spawn`'s own call
    handle = pending.await_identity(5.0)

    assert handle.session_id == "ses_self_assigned"
    assert handle.session_id != "hint-never-honored"
    wait_for_exit(handle.pid)


def test_fresh_spawn_writes_stderr_to_the_injected_path(tmp_path: Path, spawn_executor: Executor) -> None:
    """``spawn`` honors ``preamble.stderr_path`` the same way Claude Code's binding does —
    every OpenCode failure event otherwise reports an empty stderr tail even though the
    runner already allocated the file."""
    binary = worker_binary(tmp_path, minted_session_id="ses_minted_abc", stderr_message="diagnostic-sentinel")
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    stderr_path = tmp_path / "lease-1.stderr"
    adapter = _adapter(spawn_executor, binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    pending = adapter.spawn(
        envelope,
        _preamble(str(workdir), stdout_path=str(stdout_path), stderr_path=str(stderr_path)),
        session_hint="hint",
    )
    pending.confirm_durable()  # real component tests stand in for `Spawner.spawn`'s own call
    handle = pending.await_identity(5.0)
    wait_for_exit(handle.pid)

    assert "diagnostic-sentinel" in stderr_path.read_text()


def test_fresh_spawn_raises_identity_error_on_malformed_first_record(tmp_path: Path, spawn_executor: Executor) -> None:
    binary = worker_binary(tmp_path, malformed_first_line=True)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(spawn_executor, binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    pending = adapter.spawn(envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint")
    pending.confirm_durable()  # real component tests stand in for `Spawner.spawn`'s own call

    with pytest.raises(WorkerIdentityError):
        pending.await_identity(5.0)


def test_fresh_spawn_raises_identity_error_when_the_process_exits_first(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    binary = worker_binary(tmp_path, exit_before_output=True)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(spawn_executor, binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    pending = adapter.spawn(envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint")
    pending.confirm_durable()  # real component tests stand in for `Spawner.spawn`'s own call

    with pytest.raises(WorkerIdentityError):
        pending.await_identity(5.0)


def test_fresh_spawn_identity_error_carries_the_workers_own_stderr(tmp_path: Path, spawn_executor: Executor) -> None:
    """A dead-before-identity worker's real cause lives only in its own stderr capture —
    surfaced on the raised error, since it is otherwise lost the moment the lease is marked
    identity-failed and the group is killed."""
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    stdout_path.write_bytes(b"")
    stderr_path = tmp_path / "lease-1.stderr"
    stderr_path.write_text("Traceback (most recent call last):\nRuntimeError: mock-opencode blew up\n")
    probe = FakeProbe(alive=set())  # already gone
    pending = PendingOpenCodeIdentity(
        pid=4242,
        pgid=4242,
        process_start_time="start-token",
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        process=probe,
    )

    with pytest.raises(WorkerIdentityError, match="mock-opencode blew up"):
        pending.await_identity(0.1)


def test_fresh_spawn_raises_identity_error_on_timeout(tmp_path: Path, spawn_executor: Executor) -> None:
    """A live process that has written nothing yet is a plain timeout, not a crash."""
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    stdout_path.write_bytes(b"")
    probe = FakeProbe(alive={(4242, "start-token")})
    pending = PendingOpenCodeIdentity(
        pid=4242,
        pgid=4242,
        process_start_time="start-token",
        stdout_path=str(stdout_path),
        stderr_path="",
        process=probe,
    )

    with pytest.raises(WorkerIdentityError):
        pending.await_identity(0.1)


def test_fresh_spawn_succeeds_when_the_process_already_exited_after_flushing_identity(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    """A worker that flushed its identity record then exited fast is a SUCCESS — the
    valid record wins over liveness, so this must not raise even though
    the process is already dead by the time ``await_identity`` looks."""
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    stdout_path.write_bytes(
        json.dumps(
            {
                "type": "step_start",
                "sessionID": "ses_flushed",
                "part": {"id": "prt_start", "sessionID": "ses_flushed", "messageID": "msg_1", "type": "step-start"},
            }
        ).encode()
        + b"\n"
    )
    probe = FakeProbe(alive=set())  # already gone
    pending = PendingOpenCodeIdentity(
        pid=4242,
        pgid=4242,
        process_start_time="start-token",
        stdout_path=str(stdout_path),
        stderr_path="",
        process=probe,
    )

    handle = pending.await_identity(0.1)

    assert handle.session_id == "ses_flushed"


class _ExitsAtTheProbe(FakeProbe):
    """A worker that flushes its identity record and exits just as its liveness is probed."""

    def __init__(self, stdout_path: Path, record: bytes) -> None:
        super().__init__()
        self._stdout_path = stdout_path
        self._record = record

    def is_alive(self, pid: int, process_start_time: str) -> bool:
        self._stdout_path.write_bytes(self._record)
        return False


def test_fresh_spawn_reads_the_record_a_worker_flushed_as_it_exited(tmp_path: Path) -> None:
    """A worker that writes its record and dies between a poll's read and its probe still
    identifies — the read follows the probe, so it sees everything a dead worker wrote."""
    stdout_path = tmp_path / "lease-1.stdout"
    stdout_path.write_bytes(b"")
    record = (
        json.dumps(
            {
                "type": "step_start",
                "sessionID": "ses_flushed_at_exit",
                "part": {
                    "id": "prt_start",
                    "sessionID": "ses_flushed_at_exit",
                    "messageID": "msg_1",
                    "type": "step-start",
                },
            }
        ).encode()
        + b"\n"
    )
    pending = PendingOpenCodeIdentity(
        pid=4242,
        pgid=4242,
        process_start_time="start-token",
        stdout_path=str(stdout_path),
        stderr_path="",
        process=_ExitsAtTheProbe(stdout_path, record),
    )

    handle = pending.await_identity(0.1)

    assert handle.session_id == "ses_flushed_at_exit"


def test_first_event_tolerates_leading_non_json_lines(tmp_path: Path, spawn_executor: Executor) -> None:
    """Identity arrives on the worker's own SHARED stdout — an earlier writer (a tool
    banner, a stray line) may put non-JSON ahead of the real first record. Byte zero need
    not be it: the handshake skips leading noise and finds identity further down."""
    stdout_path = tmp_path / "lease-1.stdout"
    identity_line = json.dumps(
        {
            "type": "step_start",
            "sessionID": "ses_after_banner",
            "part": {"id": "prt_start", "sessionID": "ses_after_banner", "messageID": "msg_1", "type": "step-start"},
        }
    )
    stdout_path.write_text(f"A tool banner opencode never asked for\nnot json either\n{identity_line}\n")
    probe = FakeProbe(alive={(4242, "start-token")})
    pending = PendingOpenCodeIdentity(
        pid=4242,
        pgid=4242,
        process_start_time="start-token",
        stdout_path=str(stdout_path),
        stderr_path="",
        process=probe,
    )

    handle = pending.await_identity(1.0)

    assert handle.session_id == "ses_after_banner"


def test_first_event_raises_once_the_leading_noise_bound_is_exceeded(tmp_path: Path, spawn_executor: Executor) -> None:
    """The leading-noise tolerance is bounded: a permanently noisy stream with no valid
    identity in the first ``MAX_IDENTITY_PREAMBLE_LINES`` lines fails the handshake
    outright, rather than waiting on ``await_identity``'s own timeout."""
    stdout_path = tmp_path / "lease-1.stdout"
    stdout_path.write_text("not json\n" * (MAX_IDENTITY_PREAMBLE_LINES + 1))
    probe = FakeProbe(alive={(4242, "start-token")})
    pending = PendingOpenCodeIdentity(
        pid=4242,
        pgid=4242,
        process_start_time="start-token",
        stdout_path=str(stdout_path),
        stderr_path="",
        process=probe,
    )

    with pytest.raises(WorkerIdentityError):
        pending.await_identity(1.0)


def test_resume_spawn_never_performs_the_handshake(tmp_path: Path, spawn_executor: Executor) -> None:
    """A resume already knows its session id — no polling, an instant `WorkerHandle`."""
    binary = worker_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(spawn_executor, binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    pending = adapter.spawn(
        envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint=None, resume_from="ses_prior"
    )
    pending.confirm_durable()  # real component tests stand in for `Spawner.spawn`'s own call

    handle = pending.await_identity(0)  # already a `WorkerHandle` — trivial phase two
    assert handle.session_id == "ses_prior"
    wait_for_exit(handle.pid)


def test_judge_and_resume_with_message_launch_against_the_recorded_session(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    binary = worker_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(spawn_executor, binary=binary, process=LinuxProcessProbe())

    judge_handle = adapter.judge(str(workdir), "ses_recorded", "assess", str(workdir / "judge-output.json"))
    judge_handle.confirm_durable()  # real component tests stand in for `Judgement._elicit`'s own call
    wait_for_exit(judge_handle.pid)
    output = Path(workdir / "judge-output.json").read_text()
    assert adapter.parse_verdict(output) == "pass"

    resumed = adapter.resume_with_message(
        str(workdir), "ses_recorded", "continue", stdout_path=str(workdir / "nudge.out")
    )
    resumed.confirm_durable()  # component tests stand in for `_wake`'s call (src/blizzard/runner/lifecycle/dormant.py)
    wait_for_exit(resumed.pid)
    assert (workdir / "nudge.out").exists()


def test_spawn_launches_through_the_process_launcher_with_its_own_group(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    binary = worker_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(spawn_executor, binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    pending = adapter.spawn(envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint")
    pending.confirm_durable()  # real component tests stand in for `Spawner.spawn`'s own call
    handle = pending.await_identity(5.0)

    assert handle.pgid is not None
    assert handle.pgid == handle.pid  # `start_new_session=True`'s own POSIX contract
    wait_for_exit(handle.pid)


# --------------------------------------------------------------------------- #
# Model pinned at fresh mint only (component-level argv proof via a captured Popen).

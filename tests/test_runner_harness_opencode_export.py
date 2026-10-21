"""``src/blizzard/runner/harness/opencode/transcript/export.py`` — the ``opencode export`` child seam (unit).

Mirrors ``tests/test_runner_harness_adapter.py``'s own ``observe_version`` coverage: a scripted
launch seam, never a real ``opencode`` binary."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO, Any

import pytest

from blizzard.runner.harness.opencode.transcript.export import OpenCodeExportError, SubprocessOpenCodeExporter
from blizzard.runner.process.child_env import AllowlistedEnv, ChildEnv
from blizzard.runner.process.child_processes import ChildRun, ShellCommand


class _ScriptedChildren:
    """A launch seam whose ``run`` records its call and answers through ``reply``."""

    def __init__(self, reply: Callable[[IO[bytes] | None], ChildRun]) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        command: Sequence[str] | ShellCommand,
        *,
        cwd: Path | str | None,
        env: ChildEnv,
        timeout: float,
        input: str | None = None,
        input_settle: float = 0.0,
        stdout: IO[bytes] | None = None,
    ) -> ChildRun:
        self.calls.append({"command": command, "cwd": cwd, "env": env.variables, "input": input, "stdout": stdout})
        return self.reply(stdout)


def _writes(text: str, *, exit_code: int = 0, stderr: str = "") -> Callable[[IO[bytes] | None], ChildRun]:
    def reply(stdout: IO[bytes] | None) -> ChildRun:
        assert stdout is not None
        stdout.write(text.encode())
        return ChildRun(exit_code=exit_code, stdout="", stderr=stderr, timed_out=False)

    return reply


def _exporter(children: _ScriptedChildren, worker_env: AllowlistedEnv | None = None, **kw: Any) -> Any:
    env = (worker_env or AllowlistedEnv.of(())).tool()
    return SubprocessOpenCodeExporter(binary="opencode", children=children, env=env, **kw)  # type: ignore[arg-type]


@pytest.mark.unit
def test_export_returns_stdout_on_success() -> None:
    children = _ScriptedChildren(_writes('{"info": {}}'))

    assert _exporter(children).export("sess-1") == '{"info": {}}'
    call = children.calls[0]
    assert call["command"] == ["opencode", "export", "sess-1"]
    assert call["cwd"] is None  # no cwd — export resolves by id alone
    assert call["input"] is None  # stdin closed, never the daemon's own


@pytest.mark.unit
def test_export_captures_stdout_through_a_file_never_a_pipe() -> None:
    """The regression this seam exists to prevent: a piped capture silently truncates a real
    ``opencode export``'s output past the kernel pipe buffer (64 KiB on Linux)."""
    children = _ScriptedChildren(_writes("x" * 200_000))

    assert len(_exporter(children).export("sess-1")) == 200_000
    assert children.calls[0]["stdout"] is not None


@pytest.mark.unit
def test_export_env_excludes_the_hub_token_and_an_unlisted_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    """`opencode export` is a plugin-capable third-party CLI — it gets the
    same allowlisted env every other harness-binary launch does, never a full `os.environ`
    copy carrying the runner daemon's own hub credential."""
    monkeypatch.setenv("BZ_HUB_TOKEN", "super-secret-token")
    monkeypatch.setenv("MY_UNLISTED_SENTINEL_VAR", "should-not-leak")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    children = _ScriptedChildren(_writes("{}"))

    _exporter(children).export("sess-1")

    env = children.calls[0]["env"]
    assert "BZ_HUB_TOKEN" not in env
    assert "MY_UNLISTED_SENTINEL_VAR" not in env
    assert env["PATH"] == "/usr/bin:/bin"


@pytest.mark.unit
def test_export_env_passthrough_admits_an_operator_named_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_HARNESS_QUIRK", "needed-by-the-real-binary")
    children = _ScriptedChildren(_writes("{}"))

    _exporter(children, AllowlistedEnv.of(("MY_HARNESS_QUIRK",))).export("sess-1")

    assert children.calls[0]["env"]["MY_HARNESS_QUIRK"] == "needed-by-the-real-binary"


@pytest.mark.unit
def test_export_nonzero_exit_raises_with_a_stderr_tail() -> None:
    children = _ScriptedChildren(_writes("", exit_code=1, stderr="session gone"))

    with pytest.raises(OpenCodeExportError, match="session gone"):
        _exporter(children).export("sess-1")


@pytest.mark.unit
def test_export_timeout_raises() -> None:
    children = _ScriptedChildren(lambda _stdout: ChildRun(exit_code=None, stdout="", stderr="", timed_out=True))

    with pytest.raises(OpenCodeExportError, match="timed out"):
        _exporter(children, timeout=5).export("sess-1")


@pytest.mark.unit
def test_export_missing_binary_raises() -> None:
    def _missing(_stdout: IO[bytes] | None) -> ChildRun:
        raise OSError("no such file or directory: 'opencode'")

    with pytest.raises(OpenCodeExportError):
        _exporter(_ScriptedChildren(_missing)).export("sess-1")

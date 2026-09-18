"""``harness/internal/opencode_export.py`` — the ``opencode export`` subprocess seam (unit).

Mirrors ``test_runner_harness_adapter.py``'s own ``observe_version`` coverage: a scripted
``subprocess.run``, never a real ``opencode`` binary."""

from __future__ import annotations

import subprocess

import pytest

from blizzard.runner.harness.internal.opencode_export import OpenCodeExportError, SubprocessOpenCodeExporter


@pytest.mark.unit
def test_export_returns_stdout_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        kwargs["stdout"].write('{"info": {}}')  # type: ignore[union-attr]  # a real file, per the export seam
        return subprocess.CompletedProcess(cmd, 0, stdout=None, stderr="")

    monkeypatch.setattr(subprocess, "run", _run)
    exporter = SubprocessOpenCodeExporter(binary="opencode")

    assert exporter.export("sess-1") == '{"info": {}}'
    assert captured["cmd"] == ["opencode", "export", "sess-1"]
    assert "cwd" not in captured["kwargs"]  # type: ignore[operator]  # no cwd — export resolves by id alone


@pytest.mark.unit
def test_export_captures_stdout_through_a_file_never_a_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression this seam exists to prevent: a piped capture silently truncates a real
    ``opencode export``'s output past the kernel pipe buffer (64 KiB on Linux)."""
    captured: dict[str, object] = {}

    def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["kwargs"] = kwargs
        kwargs["stdout"].write("x" * 200_000)  # type: ignore[union-attr]  # past one pipe buffer
        return subprocess.CompletedProcess(cmd, 0, stdout=None, stderr="")

    monkeypatch.setattr(subprocess, "run", _run)
    exporter = SubprocessOpenCodeExporter(binary="opencode")

    assert len(exporter.export("sess-1")) == 200_000
    assert "capture_output" not in captured["kwargs"]  # type: ignore[operator]
    assert captured["kwargs"]["stdout"] is not subprocess.PIPE  # type: ignore[index]


@pytest.mark.unit
def test_export_nonzero_exit_raises_with_a_stderr_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, stdout=None, stderr="session gone")
    )
    exporter = SubprocessOpenCodeExporter(binary="opencode")

    with pytest.raises(OpenCodeExportError, match="session gone"):
        exporter.export("sess-1")


@pytest.mark.unit
def test_export_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _hung(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="opencode", timeout=5)

    monkeypatch.setattr(subprocess, "run", _hung)
    exporter = SubprocessOpenCodeExporter(binary="opencode", timeout=5)

    with pytest.raises(OpenCodeExportError):
        exporter.export("sess-1")


@pytest.mark.unit
def test_export_missing_binary_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("no such file or directory: 'opencode'")

    monkeypatch.setattr(subprocess, "run", _missing)
    exporter = SubprocessOpenCodeExporter(binary="opencode")

    with pytest.raises(OpenCodeExportError):
        exporter.export("sess-1")

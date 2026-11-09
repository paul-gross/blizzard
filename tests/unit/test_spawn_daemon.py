from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tests.support.scaffolding import spawn_daemon


def _parent_descriptors_for(path: Path) -> list[Path]:
    target = path.stat()
    descriptors: list[Path] = []
    for descriptor in Path("/proc/self/fd").iterdir():
        try:
            opened = descriptor.stat()
        except FileNotFoundError:
            continue
        if (opened.st_dev, opened.st_ino) == (target.st_dev, target.st_ino):
            descriptors.append(descriptor)
    return descriptors


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux /proc/self/fd")
def test_spawn_daemon_redirects_output_and_closes_parent_descriptor(tmp_path: Path) -> None:
    log = tmp_path / "daemon.log"
    script = 'import sys; print("stdout", flush=True); print("stderr", file=sys.stderr, flush=True)'
    process = spawn_daemon([sys.executable, "-c", script], log=log, env={**os.environ}, start_new_session=True)
    try:
        assert _parent_descriptors_for(log) == []
        assert process.wait(timeout=10) == 0
        assert set(log.read_text().splitlines()) == {"stdout", "stderr"}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux /proc/self/fd")
def test_spawn_daemon_closes_descriptor_when_spawn_fails(tmp_path: Path) -> None:
    log = tmp_path / "daemon.log"
    with pytest.raises(FileNotFoundError):
        spawn_daemon([str(tmp_path / "missing-executable")], log=log)
    assert log.exists()
    assert _parent_descriptors_for(log) == []

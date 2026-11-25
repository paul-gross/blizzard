"""Only runner sockets leave pytest's scratch tree, including in spawned daemons."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

from blizzard.runner.host_layout import HostLayout


def test_subprocess_socket_is_short_and_workspace_stays_in_tmp_path(
    tmp_path: Path, short_subprocess_sockets: None
) -> None:
    host = tmp_path / "host"
    host.mkdir()
    (host / "daemon.log").write_text("diagnostic")
    socket_path = HostLayout(host).socket_path
    assert len(os.fsencode(socket_path)) < 108
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; from blizzard.runner.host_layout import HostLayout;"
            " print(HostLayout(Path(sys.argv[1])).socket_path)",
            str(host),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert Path(result.stdout.strip()) == socket_path
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(socket_path))
    assert (host / "daemon.log").read_text() == "diagnostic"
    assert host.resolve().is_relative_to(tmp_path.resolve())
    if os.environ.get("TMPDIR") == os.environ.get("BLIZZARD_TMPDIR") and os.environ.get("TMPDIR"):
        assert host.resolve().is_relative_to(Path(os.environ["TMPDIR"]).resolve())

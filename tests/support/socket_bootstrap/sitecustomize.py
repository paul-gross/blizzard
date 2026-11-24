"""Redirect only test runner sockets in subprocesses; their stores stay in the test scratch tree."""

import hashlib
import os
from pathlib import Path

from blizzard.runner.host_layout import HostLayout

_runtime = Path(os.environ["BLIZZARD_TEST_SOCKET_DIR"])


def _socket(layout: HostLayout, name: str) -> Path:
    key = hashlib.sha256(os.fsencode(layout.root.resolve())).hexdigest()[:16]
    directory = _runtime / key
    directory.mkdir(exist_ok=True)
    return directory / name


HostLayout.socket_path = property(lambda layout: _socket(layout, "host.sock"))  # pyright: ignore[reportAttributeAccessIssue]
HostLayout.legacy_socket_path = property(lambda layout: _socket(layout, "runner.sock"))  # pyright: ignore[reportAttributeAccessIssue]

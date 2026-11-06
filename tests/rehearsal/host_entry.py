"""Start an installed host with its socket reached through an open directory descriptor."""

import os
import sys
from importlib.metadata import entry_points
from pathlib import Path

from blizzard.runner.host_layout import HostLayout

root = Path(sys.argv.pop(1))
fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
HostLayout.socket_path = property(lambda self: Path(f"/proc/self/fd/{fd}/host.sock"))  # pyright: ignore[reportAttributeAccessIssue]

next(iter(entry_points(group="console_scripts", name="blizzard-runner"))).load()()

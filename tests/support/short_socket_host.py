"""Launch the installed runner host with its socket in a test-owned runtime directory."""

import sys
from importlib.metadata import entry_points
from pathlib import Path

from blizzard.runner.host_layout import HostLayout

runtime = Path(sys.argv.pop(1))
HostLayout.socket_path = property(lambda self: runtime / "host.sock")  # pyright: ignore[reportAttributeAccessIssue]
next(iter(entry_points(group="console_scripts", name="blizzard-runner"))).load()()

"""A minimal fake ``opencode`` CLI for ``tests/test_runner_harness_opencode_adapter.py``'s
own unit/component tests — distinct from the compatibility diagnostic's own proof
scaffolding (``tests/service/test_opencode_compatibility_service.py``,
``bzh:external-cli-fake-is-service-tier``, driven off a real ``mock-opencode emit``
artifact instead)."""

from __future__ import annotations

import stat
from pathlib import Path

_FAKE_WORKER_OPENCODE = r"""#!/usr/bin/env python3
import json
import sys

MALFORMED_FIRST_LINE = False
EXIT_BEFORE_OUTPUT = False
MINTED_SESSION_ID = "ses_minted"
STDERR_MESSAGE = ""

args = sys.argv[1:]
if args == ["--version"]:
    print("opencode 1.18.25")
    raise SystemExit(0)

if not args or args[0] != "run":
    print(f"unsupported invocation: {args}", file=sys.stderr)
    raise SystemExit(2)

if STDERR_MESSAGE:
    print(STDERR_MESSAGE, file=sys.stderr, flush=True)

if EXIT_BEFORE_OUTPUT:
    raise SystemExit(1)

if MALFORMED_FIRST_LINE:
    print("not json at all")
    raise SystemExit(1)

session_id = MINTED_SESSION_ID
if "--session" in args:
    session_id = args[args.index("--session") + 1]

events = [
    {"type": "step_start", "sessionID": session_id,
     "part": {"id": "prt_start", "sessionID": session_id, "messageID": "msg_1", "type": "step-start"}},
    {"type": "text", "sessionID": session_id,
     "part": {"id": "prt_text", "sessionID": session_id, "messageID": "msg_1", "type": "text",
              "text": "Done. <Choice>pass</Choice>"}},
    {"type": "step_finish", "sessionID": session_id,
     "part": {"id": "prt_finish", "sessionID": session_id, "messageID": "msg_1", "type": "step-finish",
              "reason": "stop", "cost": 0.01,
              "tokens": {"input": 2, "output": 3, "reasoning": 0, "cache": {"read": 0, "write": 0}}}},
]
for event in events:
    print(json.dumps(event), flush=True)
"""


def worker_binary(
    tmp_path: Path,
    *,
    malformed_first_line: bool = False,
    exit_before_output: bool = False,
    minted_session_id: str = "ses_minted",
    stderr_message: str = "",
) -> str:
    """A minimal, well-behaved fake ``opencode`` CLI for the adapter's own unit/component
    tests (``tests/test_runner_harness_opencode_adapter.py``)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = _FAKE_WORKER_OPENCODE.replace(
        "MALFORMED_FIRST_LINE = False", f"MALFORMED_FIRST_LINE = {malformed_first_line}"
    )
    source = source.replace("EXIT_BEFORE_OUTPUT = False", f"EXIT_BEFORE_OUTPUT = {exit_before_output}")
    source = source.replace('MINTED_SESSION_ID = "ses_minted"', f"MINTED_SESSION_ID = {minted_session_id!r}")
    source = source.replace('STDERR_MESSAGE = ""', f"STDERR_MESSAGE = {stderr_message!r}")
    path = tmp_path / "fake-worker-opencode"
    path.write_text(source)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return str(path)


__all__ = ["worker_binary"]

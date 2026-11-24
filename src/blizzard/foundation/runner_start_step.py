"""The startup step a runner start failed at — one definition, shared by the host's start failures and its wire."""

from __future__ import annotations

from typing import Literal

#: The startup step a runner start failed at, as a failed start and the panel name it.
RunnerStartStep = Literal[
    "seed", "compose", "worker_shell", "tracing", "worker_scratch", "resume", "takeovers", "sessions", "serve"
]

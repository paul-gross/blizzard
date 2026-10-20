"""The runner host's per-runner state vocabulary — one definition, shared by the host's wire and its API."""

from __future__ import annotations

from typing import Literal

#: Whether the host serves a runner, or holds it halted by its own configuration errors or its hub's refusal.
HostRunnerState = Literal["serving", "halted"]

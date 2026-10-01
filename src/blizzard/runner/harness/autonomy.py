"""The runner-wide harness autonomy setting.

One harness-neutral value (``[harness] autonomy`` in ``blizzard-runner.toml``); each harness
binding translates it into its own permission vocabulary. Dependency-free and outside
``harness/internal/`` so the config module can import it."""

from __future__ import annotations

from enum import StrEnum


class Autonomy(StrEnum):
    """How freely an unattended worker may act without a human approving tool use."""

    Normal = "normal"
    Auto = "auto"
    Dangerous = "dangerous"

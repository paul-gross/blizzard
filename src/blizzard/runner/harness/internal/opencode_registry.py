"""Production construction for the OpenCode coding harness (D9).

The one factory allowed to construct :class:`OpenCodeAdapter` — every composition root takes
the registry `harness_registry.build_production_harness_registry` builds instead, and
`tests/test_layering.py` fails if the concrete class is named anywhere else."""

from __future__ import annotations

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.internal.opencode_adapter import OpenCodeAdapter
from blizzard.runner.harness.registry import HarnessBinding
from blizzard.runner.loop.process import LinuxProcessProbe


def build_opencode_binding(config: RunnerConfig) -> HarnessBinding:
    """Build the OpenCode adapter once for one composition graph.

    No transcript source yet: OpenCode transcript reading is a later phase's own capability,
    and a harness with none binds a null source so no caller needs a null check."""
    adapter = OpenCodeAdapter(
        binary=config.opencode_binary,
        env_passthrough=config.worker_env_passthrough,
        model_aliases=config.opencode_model_aliases,
        effort_aliases=config.opencode_effort_aliases,
        worker_config_path=config.opencode_worker_config_path,
        process=LinuxProcessProbe(),
    )
    return HarnessBinding(adapter=adapter)


__all__ = ["build_opencode_binding"]

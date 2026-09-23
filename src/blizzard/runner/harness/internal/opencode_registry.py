"""Production construction for the OpenCode coding harness (D9).

The one factory allowed to construct :class:`OpenCodeAdapter` — every composition root takes
the registry `harness_registry.build_production_harness_registry` builds instead, and
`tests/test_layering.py` fails if the concrete class is named anywhere else."""

from __future__ import annotations

from blizzard.foundation.logging import get_logger
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.internal.opencode_adapter import OpenCodeAdapter
from blizzard.runner.harness.internal.opencode_export import SubprocessOpenCodeExporter
from blizzard.runner.harness.internal.opencode_price_cache import FileOpenCodePriceCatalog, resolve_price_cache_path
from blizzard.runner.harness.internal.opencode_transcript_source import OpenCodeTranscriptSource
from blizzard.runner.harness.process_launch import IProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding
from blizzard.runner.harness.transcript import TranscriptErrorFactory
from blizzard.runner.loop.process import IProcessProbe


def build_opencode_binding(
    config: RunnerConfig, *, process: IProcessProbe, launcher: IProcessLauncher
) -> HarnessBinding:
    """Build the OpenCode adapter once for one composition graph, over the one runner-owned
    ``process``/``launcher`` pair the Claude Code binding also receives (D4). Wires one
    :class:`OpenCodeTranscriptSource` into both the adapter and the binding, exactly as
    Claude Code's own binding wires its transcript source, plus a price catalog resolved
    from the same worker-env passthrough — never a constant path."""
    transcript_source = OpenCodeTranscriptSource(
        SubprocessOpenCodeExporter(binary=config.opencode_binary, env_passthrough=config.worker_env_passthrough),
        TranscriptErrorFactory(get_logger("blizzard.runner.harness.transcript")),
    )
    worker_env = AllowlistedEnv.of(config.worker_env_passthrough).variables
    cache_path = resolve_price_cache_path(worker_env)
    price_catalog = FileOpenCodePriceCatalog(cache_path) if cache_path is not None else None
    adapter = OpenCodeAdapter(
        binary=config.opencode_binary,
        env_passthrough=config.worker_env_passthrough,
        model_aliases=config.opencode_model_aliases,
        effort_aliases=config.opencode_effort_aliases,
        worker_config_path=config.opencode_worker_config_path,
        transcript_source=transcript_source,
        price_catalog=price_catalog,
        process=process,
        launcher=launcher,
    )
    return HarnessBinding(adapter=adapter, transcript_source=transcript_source)


__all__ = ["build_opencode_binding"]

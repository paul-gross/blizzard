"""The OpenCode binding's declaration — the one approved site constructing
:class:`OpenCodeAdapter` and its health probe (``tests/test_layering.py``)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import HarnessLayout, HarnessSource
from blizzard.runner.harness.declaration import SharedHarnessInputs
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryNames, HarnessTelemetryPlan
from blizzard.runner.harness.identity import OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.bundle_publisher import published_snapshot
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.opencode.adapter import OpenCodeAdapter
from blizzard.runner.harness.opencode.bundle import OPENCODE_BUNDLE_LAYOUT, opencode_bundle_layout
from blizzard.runner.harness.opencode.health import OpenCodeHealthProbe
from blizzard.runner.harness.opencode.paths import resolve_opencode_auth_path
from blizzard.runner.harness.opencode.permissions.permission_resolver import SubprocessOpenCodePermissionResolver
from blizzard.runner.harness.opencode.plugin import (
    PLUGIN_DIRNAME,
    PLUGIN_FILENAME,
    plugin_reference,
    write_plugin,
)
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.opencode.transcript.export import SubprocessOpenCodeExporter
from blizzard.runner.harness.opencode.transcript.normalizer import NORMALIZER_VERSION
from blizzard.runner.harness.opencode.transcript.transcript_source import OpenCodeTranscriptSource
from blizzard.runner.harness.opencode.usage.descendant_usage import OpenCodeDescendantUsage
from blizzard.runner.harness.opencode.usage.price_cache import FileOpenCodePriceCatalog, resolve_price_cache_path
from blizzard.runner.harness.opencode.worker_config import write_worker_config
from blizzard.runner.harness.process_launch import IProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding
from blizzard.runner.harness.transcript import TranscriptErrorFactory
from blizzard.runner.process.probe import IProcessProbe


@domain_model
@dataclass(frozen=True)
class OpenCodeDeclaration:
    """OpenCode, declared once."""

    @property
    def harness_id(self) -> str:
        return OPENCODE_HARNESS_ID

    @property
    def normalizer_version(self) -> str:
        return NORMALIZER_VERSION

    @property
    def bundle_layout(self) -> HarnessLayout:
        return OPENCODE_BUNDLE_LAYOUT

    @property
    def telemetry_names(self) -> HarnessTelemetryNames | None:
        return None

    def telemetry_plan(self, section: OpenCodeSection, shared: SharedHarnessInputs) -> HarnessTelemetryPlan:
        return HarnessTelemetryPlan()

    def binding(
        self,
        section: OpenCodeSection,
        shared: SharedHarnessInputs,
        *,
        process: IProcessProbe,
        launcher: IProcessLauncher,
    ) -> HarnessBinding:
        """Build the adapter over the one runner-owned ``process``/``launcher`` pair every binding
        receives. One :class:`OpenCodeTranscriptSource` backs both the adapter and the binding, and
        a price catalog is resolved from the same worker env — never a constant path. One exporter
        backs both readers."""
        worker_env = shared.worker_env
        exporter = SubprocessOpenCodeExporter(binary=section.binary, worker_env=worker_env)
        transcript_source = OpenCodeTranscriptSource(
            exporter,
            TranscriptErrorFactory(get_logger("blizzard.runner.harness.transcript")),
        )
        cache_path = resolve_price_cache_path(worker_env.variables)
        price_catalog = FileOpenCodePriceCatalog(cache_path) if cache_path is not None else None
        snapshot = published_snapshot(shared.root) if shared.config_dir is not None else None
        bundled = snapshot is not None and (snapshot / "opencode" / "opencode.json").is_file()
        adapter = OpenCodeAdapter(
            binary=section.binary,
            worker_env=worker_env,
            model_aliases=section.model_aliases,
            effort_aliases=section.effort_aliases,
            worker_config_path=(
                str(snapshot / "opencode" / "opencode.json")
                if bundled and snapshot is not None
                else section.worker_config_path
            ),
            effective_config_dir=str(snapshot / "opencode") if bundled and snapshot is not None else None,
            autonomy=shared.autonomy,
            transcript_source=transcript_source,
            price_catalog=price_catalog,
            descendant_usage=OpenCodeDescendantUsage(exporter),
            permission_resolver=SubprocessOpenCodePermissionResolver(binary=section.binary),
            process=process,
            launcher=launcher,
        )
        return HarnessBinding(adapter=adapter, transcript_source=transcript_source)

    def health_probe(
        self, section: OpenCodeSection, shared: SharedHarnessInputs, *, spawn_root: str
    ) -> IHarnessHealthProbe:
        # The file a spawned worker would read: its allowlisted env, not the daemon's own.
        auth_path = (
            Path(section.auth_path)
            if section.auth_path is not None
            else resolve_opencode_auth_path(shared.worker_env.variables)
        )
        return OpenCodeHealthProbe(binary=section.binary, auth_path=auth_path, corpus=CommittedCorpus())

    def publish_layout(self, section: OpenCodeSection, *, autonomy: Autonomy, runtime_root: Path) -> HarnessLayout:
        return opencode_bundle_layout(section.worker_config_at(runtime_root))

    def scaffold_runtime(self, section: OpenCodeSection, root: Path) -> None:
        # The runner-owned plugin and the permission/plugin document referencing it, never
        # inside a project repository.
        plugin_path = root / PLUGIN_DIRNAME / PLUGIN_FILENAME
        write_plugin(plugin_path)
        write_worker_config(section.worker_config_at(root), plugins=(plugin_reference(plugin_path),))

    def diagnostics(self, section: OpenCodeSection, shared: SharedHarnessInputs, *, spawn_root: str) -> tuple[str, ...]:
        return ()

    def unbundled_status(self, section: OpenCodeSection) -> tuple[str, ...]:
        return (f"opencode worker config in effect: {section.worker_config_path}",)

    def snapshot_status(self, snapshot: Path, sources: tuple[HarnessSource, ...]) -> tuple[str, ...]:
        return ()


OPENCODE_DECLARATION = OpenCodeDeclaration()

__all__ = ["OPENCODE_DECLARATION", "OpenCodeDeclaration"]

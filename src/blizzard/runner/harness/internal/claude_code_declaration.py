"""The Claude Code binding's declaration — the one approved site constructing
:class:`ClaudeCodeAdapter` and its health probe (``tests/test_layering.py``)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.ambient_conflicts import (
    claude_code_ambient_sources,
    claude_code_config_conflicts,
    claude_code_permission_mode,
)
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import HarnessLayout, HarnessSource
from blizzard.runner.harness.declaration import SharedHarnessInputs
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.harness.internal.claude_code_bundle import (
    CLAUDE_CODE_BUNDLE_LAYOUT,
    ClaudeCodeBundleDelivery,
    claude_code_bundle_layout,
)
from blizzard.runner.harness.internal.claude_code_health import ClaudeCodeHealthProbe
from blizzard.runner.harness.internal.claude_code_normalizer import NORMALIZER_VERSION
from blizzard.runner.harness.internal.claude_code_section import WORKER_SETTINGS_FILENAME, ClaudeCodeSection
from blizzard.runner.harness.internal.claude_code_transcript import ClaudeCodeTranscriptSource
from blizzard.runner.harness.process_launch import IProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding
from blizzard.runner.harness.transcript import TranscriptErrorFactory
from blizzard.runner.harness.worker_settings import WorkerSettings
from blizzard.runner.loop.process import IProcessProbe


@dataclass(frozen=True)
class ClaudeCodeDeclaration:
    """Claude Code, declared once."""

    @property
    def harness_id(self) -> str:
        return CLAUDE_CODE_HARNESS_ID

    @property
    def normalizer_version(self) -> str:
        return NORMALIZER_VERSION

    @property
    def bundle_layout(self) -> HarnessLayout:
        return CLAUDE_CODE_BUNDLE_LAYOUT

    def binding(
        self,
        section: ClaudeCodeSection,
        shared: SharedHarnessInputs,
        *,
        process: IProcessProbe,
        launcher: IProcessLauncher,
    ) -> HarnessBinding:
        projects_root = shared.transcripts_root or str(Path.home() / ".claude" / "projects")
        transcript_source = ClaudeCodeTranscriptSource(
            projects_root, TranscriptErrorFactory(get_logger("blizzard.runner.harness.transcript"))
        )
        adapter = ClaudeCodeAdapter(
            binary=section.binary,
            settings_path=section.worker_settings_path,
            bundle=ClaudeCodeBundleDelivery.of(shared.bundle),
            autonomy=shared.autonomy,
            permission_mode=section.permission_mode,
            worker_env=shared.worker_env,
            model_aliases=section.model_aliases,
            effort_aliases=section.effort_aliases,
            transcript_source=transcript_source,
            process=process,
            launcher=launcher,
            harness_telemetry=shared.harness_telemetry,
        )
        return HarnessBinding(adapter=adapter, transcript_source=transcript_source)

    def health_probe(
        self, section: ClaudeCodeSection, shared: SharedHarnessInputs, *, spawn_root: str
    ) -> IHarnessHealthProbe:
        return ClaudeCodeHealthProbe(
            binary=section.binary,
            credentials_path=section.credentials_path,
            ambient_sources=claude_code_ambient_sources(shared.worker_env, spawn_root=spawn_root),
            permission_mode=claude_code_permission_mode(shared.autonomy, section.permission_mode),
        )

    def publish_layout(self, section: ClaudeCodeSection, *, autonomy: Autonomy, runtime_root: Path) -> HarnessLayout:
        return claude_code_bundle_layout(claude_code_permission_mode(autonomy, section.permission_mode))

    def scaffold_runtime(self, section: ClaudeCodeSection, root: Path) -> None:
        # Written idempotently: the content is versioned with the runner, so re-running
        # `init` refreshes it to head.
        (root / WORKER_SETTINGS_FILENAME).write_text(WorkerSettings.of().json)

    def diagnostics(
        self, section: ClaudeCodeSection, shared: SharedHarnessInputs, *, spawn_root: str
    ) -> tuple[str, ...]:
        if not section.enabled:
            return ()
        conflicts = claude_code_config_conflicts(
            shared.worker_env, autonomy=shared.autonomy, override=section.permission_mode, spawn_root=spawn_root
        )
        return tuple(f"claude-code config conflict: {conflict}" for conflict in conflicts)

    def unbundled_status(self, section: ClaudeCodeSection) -> tuple[str, ...]:
        return (f"worker settings file in effect: {section.worker_settings_path}",)

    def snapshot_status(self, snapshot: Path, sources: tuple[HarnessSource, ...]) -> tuple[str, ...]:
        source = next((s for s in sources if s.dirname == CLAUDE_CODE_BUNDLE_LAYOUT.dirname), None)
        if source is None:
            return ()
        delivery = ClaudeCodeBundleDelivery.at(snapshot / source.dirname, source.entry_points)
        return (
            f"claude-code effective settings: {delivery.settings}",
            f"claude-code flags: {' '.join(delivery.argv())}",
        )


CLAUDE_CODE_DECLARATION = ClaudeCodeDeclaration()

__all__ = ["CLAUDE_CODE_DECLARATION", "ClaudeCodeDeclaration"]

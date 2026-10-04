"""Claude Code's slice of the operator harness-config bundle.

Claude Code resolves relative paths against the worker's cwd, not the file that names them,
so no entry point declares a companion extractor. Publishing composes the operator's
``settings.json`` with the runner's own wiring (:mod:`.claude_code.settings_compose`) into the
snapshot copy, which is written even when the operator supplied none."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import dto
from blizzard.runner.harness.bundle import (
    BundleSnapshot,
    EntryPoint,
    HarnessBundleError,
    HarnessComposition,
    HarnessLayout,
)
from blizzard.runner.harness.claude_code.settings_compose import SettingsCollision, compose_settings
from blizzard.runner.harness.claude_code.worker_settings import WorkerSettings

_DIRNAME = "claude-code"
SETTINGS_FILENAME = "settings.json"


def claude_code_bundle_layout(permission_mode: str | None = None) -> HarnessLayout:
    """The layout, composing against the permission mode the adapter will pass."""
    return HarnessLayout(
        dirname=_DIRNAME,
        entry_points=(
            EntryPoint(SETTINGS_FILENAME),
            EntryPoint("mcp.json"),
            EntryPoint("agents.json"),
            EntryPoint("plugins", is_dir=True),
        ),
        compose=_composer(permission_mode),
    )


@dto
@dataclass(frozen=True)
class ClaudeCodeBundleDelivery:
    """The Claude Code files of a published snapshot, as the flags that deliver them."""

    settings: Path
    mcp_config: Path | None = None
    agents: Path | None = None
    plugins: Path | None = None

    @classmethod
    def of(cls, snapshot: BundleSnapshot | None) -> ClaudeCodeBundleDelivery | None:
        """The delivery for ``snapshot``, or ``None`` when it holds no Claude Code directory."""
        source = next((h for h in snapshot.harnesses if h.dirname == _DIRNAME), None) if snapshot else None
        return cls.at(source.effective_dir, source.entry_points) if source is not None else None

    @classmethod
    def at(cls, directory: Path, entry_points: tuple[str, ...]) -> ClaudeCodeBundleDelivery:
        """The delivery for a snapshot's ``claude-code`` directory and the entry points the
        operator's source supplied (the composed settings file always exists)."""
        root = directory.resolve()
        present = {name: root / name for name in entry_points}
        return cls(
            root / SETTINGS_FILENAME, present.get("mcp.json"), present.get("agents.json"), present.get("plugins")
        )

    def argv(self) -> list[str]:
        args = ["--settings", str(self.settings)]
        if self.mcp_config is not None:
            args.append(f"--mcp-config={self.mcp_config}")
        if self.agents is not None:
            args += ["--agents", str(self.agents)]
        if self.plugins is not None:
            args += ["--plugin-dir", str(self.plugins)]
        return args


def _composer(permission_mode: str | None) -> Callable[[HarnessComposition], None]:
    def compose(composition: HarnessComposition) -> None:
        staged = composition.staged_dir / SETTINGS_FILENAME
        operator = json.loads(staged.read_text()) if staged.exists() else {}
        try:
            composed = compose_settings(operator, WorkerSettings.of().document, permission_mode=permission_mode)
        except SettingsCollision as exc:
            raise HarnessBundleError(composition.source_dir / SETTINGS_FILENAME, str(exc)) from exc
        staged.write_text(json.dumps(composed, indent=2) + "\n")

    return compose


CLAUDE_CODE_BUNDLE_LAYOUT = claude_code_bundle_layout()

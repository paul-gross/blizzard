"""The packaged workspace-prompt samples, and the loader that reads them (issue #344).

Packaged data is one directory per sample, each holding its own ``workspace-prompt.md`` — the
spawn preamble's layer 2, shipped so a deployment names one instead of authoring the layer from
scratch. A sample is prose read off disk at startup, never applied unless a config knob names it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from blizzard.foundation.clock import IClock

WORKSPACE_PROMPT_FILENAME = "workspace-prompt.md"


class UnknownWorkspacePromptSample(LookupError):
    """A sample name the packaged corpus does not carry."""


@dataclass(frozen=True)
class PackagedWorkspacePrompts:
    """The workspace-prompt sample set shipped in this package — one directory per sample."""

    root: Path

    @property
    def names(self) -> list[str]:
        """Every packaged sample's name, sorted, so a listing over them reads the same way twice.

        The filename is the membership test, so a directory carrying no sample is skipped by
        construction rather than by a blocklist that would need maintaining."""
        return sorted(path.parent.name for path in self.root.glob(f"*/{WORKSPACE_PROMPT_FILENAME}"))

    def path(self, name: str) -> Path:
        """Where the named sample would live, whether or not the corpus carries it."""
        return self.root / name / WORKSPACE_PROMPT_FILENAME

    def text(self, name: str) -> str:
        """The named sample's prose, raising :class:`UnknownWorkspacePromptSample` when absent."""
        path = self.path(name)
        if not path.exists():
            raise UnknownWorkspacePromptSample(name)
        return path.read_text()


PACKAGED = PackagedWorkspacePrompts(Path(__file__).resolve().parent / "prompts" / "samples")


class IReadWorkspacePromptRepository(Protocol):
    """Read-only runtime workspace-prompt override queries (blizzard#410) — distinct from
    :class:`PackagedWorkspacePrompts` above, the static samples this override wins over."""

    def workspace_prompt_override(self, workspace_id: str) -> str | None:
        """The runtime workspace-prompt override for this workspace, or ``None`` (issue #17).

        ``None`` means never overridden — the caller falls back to the static config
        prompt. A present row (even an empty string) is a deliberate override that wins
        over config."""
        ...


class IWriteWorkspacePromptRepository(IReadWorkspacePromptRepository, Protocol):
    """Read-write runtime workspace-prompt override store — held only by the domain."""

    def set_workspace_prompt(self, workspace_id: str, *, prompt: str, at: datetime) -> None:
        """Set the runtime workspace-prompt override (upsert) — read at spawn (issue #17)."""
        ...

    def clear_workspace_prompt(self, workspace_id: str) -> bool:
        """Drop the runtime workspace-prompt override, returning whether one was there (#344).

        Removing the row is what distinguishes clearing from overriding with empty text: the
        absent row is the only state that resolves back to the configured prompt."""
        ...


class WorkspacePromptService:
    """Composition-root-wired: the workspace-prompt store and the clock (D4, blizzard#412)."""

    def __init__(self, store: IWriteWorkspacePromptRepository, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def replace(self, workspace_id: str, *, prompt: str) -> None:
        """Replace the runtime workspace-prompt override — effective on subsequent spawns
        (issue #17)."""
        self._store.set_workspace_prompt(workspace_id, prompt=prompt, at=self._clock.now())

    def clear(self, workspace_id: str) -> None:
        """Drop the runtime workspace-prompt override so the runner's configured prompt
        resolves again (issue #344)."""
        self._store.clear_workspace_prompt(workspace_id)

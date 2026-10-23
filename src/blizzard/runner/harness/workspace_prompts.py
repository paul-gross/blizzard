"""The packaged workspace-prompt samples, and the loader that reads them.

Packaged data is one directory per sample, each holding its own ``workspace-prompt.md`` — the
spawn preamble's layer 2, shipped so a deployment names one instead of authoring the layer from
scratch. A sample is prose read off disk at startup, never applied unless a config knob names it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, Self

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import collaborator, domain_model

WORKSPACE_PROMPT_FILENAME = "workspace-prompt.md"


class UnknownWorkspacePromptSample(LookupError):
    """A sample name the packaged corpus does not carry."""


@collaborator
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
    """Read-only runtime workspace-prompt override queries — distinct from
    :class:`PackagedWorkspacePrompts` above, the static samples this override wins over."""

    def workspace_prompt_override(self, workspace_id: str) -> str | None:
        """The runtime workspace-prompt override for this workspace, or ``None``.

        ``None`` means never overridden; a present row, even an empty string, is a deliberate
        override."""
        ...


class IWriteWorkspacePromptRepository(IReadWorkspacePromptRepository, Protocol):
    """Read-write runtime workspace-prompt override store."""

    def set_workspace_prompt(self, workspace_id: str, *, prompt: str, at: datetime) -> None:
        """Set the runtime workspace-prompt override (upsert)."""
        ...

    def clear_workspace_prompt(self, workspace_id: str) -> bool:
        """Drop the runtime workspace-prompt override, returning whether one was there.

        Removing the row is what distinguishes clearing from overriding with empty text: the
        absent row is the only state that resolves back to the configured prompt."""
        ...


class IConfiguredWorkspacePrompts(Protocol):
    """The static configured prompt per workspace — the lane an override wins over."""

    def prompt(self, workspace_id: str | None) -> str:
        """The workspace's configured standing prompt; ``""`` for no workspace or an undeclared one."""
        ...


@domain_model
@dataclass(frozen=True)
class EffectiveWorkspacePrompt:
    """The prompt a spawn reads, and the lane that produced it."""

    prompt: str
    source: Literal["override", "config"]

    @classmethod
    def resolve(cls, override: str | None, configured: str) -> Self:
        """The override when one stands — an empty string is still one — else the configured prompt."""
        if override is not None:
            return cls(prompt=override, source="override")
        return cls(prompt=configured, source="config")


class WorkspacePromptReader:
    """The one owner of which prompt a workspace's spawns read: the store's runtime override, else the configured
    prompt. Composition-root-wired: the read-only override store and the configured lane."""

    def __init__(self, overrides: IReadWorkspacePromptRepository, configured: IConfiguredWorkspacePrompts) -> None:
        self._overrides = overrides
        self._configured = configured

    def effective(self, workspace_id: str | None) -> EffectiveWorkspacePrompt:
        """The workspace's effective prompt, read fresh so a replace applies to the next spawn with no restart. A
        binding naming no workspace has no override."""
        override = self._overrides.workspace_prompt_override(workspace_id) if workspace_id is not None else None
        return EffectiveWorkspacePrompt.resolve(override, self._configured.prompt(workspace_id))


class WorkspacePromptService:
    """Composition-root-wired: the workspace-prompt store and the clock."""

    def __init__(self, store: IWriteWorkspacePromptRepository, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def replace(self, workspace_id: str, *, prompt: str) -> None:
        """Replace the runtime workspace-prompt override — effective on subsequent spawns."""
        self._store.set_workspace_prompt(workspace_id, prompt=prompt, at=self._clock.now())

    def clear(self, workspace_id: str) -> None:
        """Drop the runtime workspace-prompt override so the runner's configured prompt
        resolves again."""
        self._store.clear_workspace_prompt(workspace_id)

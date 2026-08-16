"""The packaged workspace-prompt samples, and the loader that reads them (issue #344).

Packaged data is one directory per sample, each holding its own ``workspace-prompt.md`` — the
spawn preamble's layer 2, shipped so a deployment names one instead of authoring the layer from
scratch. A sample is prose read off disk at startup, never applied unless a config knob names it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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

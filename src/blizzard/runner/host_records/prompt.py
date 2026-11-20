"""A workspace's standing prompt as its record states it: the ``prompt``, ``prompt_file`` and ``prompt_package``
settings, and what they resolve to."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from blizzard.foundation.roles import domain_model

#: The prompt settings of a workspace record, each returned to its default when a route clears the prompt.
PROMPT_SETTINGS = frozenset({"prompt", "prompt_file", "prompt_package"})


@domain_model
@dataclass(frozen=True)
class EffectiveWorkspacePrompt:
    """The prompt a spawn reads: its text, whether the workspace states one (``config``) or none (``default``), and
    the setting it resolved from, in words."""

    prompt: str
    source: Literal["config", "default"]
    origin: str = "none"


@domain_model
@dataclass(frozen=True)
class PromptChange:
    """An edit of a workspace's prompt: stating one prompt setting returns the other two to their defaults, so the
    record states at most one; clearing returns all three."""

    sets: Mapping[str, object] = field(default_factory=dict[str, object])

    @classmethod
    def stating(cls, setting: str, value: object) -> PromptChange:
        """State ``setting``, one of :data:`PROMPT_SETTINGS`, as ``value``; the other two return to their defaults."""
        if setting not in PROMPT_SETTINGS:
            raise ValueError(f"{setting!r} is not a prompt setting")
        return cls({setting: value})

    @classmethod
    def cleared(cls) -> PromptChange:
        """Return all three prompt settings to their defaults."""
        return cls()

    @property
    def unsets(self) -> frozenset[str]:
        """The prompt settings the change returns to their defaults."""
        return PROMPT_SETTINGS - self.sets.keys()

    def body(self) -> dict[str, object]:
        """The change as a sparse edit's body, ``None`` returning a setting to its default."""
        return {**dict.fromkeys(sorted(self.unsets)), **self.sets}

    def applied_to(self, settings: Mapping[str, object]) -> dict[str, object]:
        """``settings`` with the change made."""
        return {**{name: value for name, value in settings.items() if name not in PROMPT_SETTINGS}, **self.sets}

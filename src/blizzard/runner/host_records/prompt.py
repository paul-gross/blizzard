"""A workspace's standing prompt as its record states it: the ``prompt``, ``prompt_file`` and ``prompt_package``
settings, and what they resolve to."""

from __future__ import annotations

from dataclasses import dataclass
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

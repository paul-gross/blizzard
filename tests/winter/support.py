"""The winter-binding tier's gate and winter-source discovery.

The tier proves the winter workspace provider against the real ``winter`` CLI. It is local-only: it runs when
``BLIZZARD_WINTER_BINDING=1`` and a winter source — a workspace holding ``.winter/config.toml`` and
``tools/winter-cli`` — is found, from ``$BLIZZARD_MOCK_WINTER_SOURCE`` or by walking up from this repository.
Source discovery lives only here; the platform tiers (service, e2e, crash) never look for winter.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

GATE_VAR = "BLIZZARD_WINTER_BINDING"
SOURCE_VAR = "BLIZZARD_MOCK_WINTER_SOURCE"

winter_binding_gate = pytest.mark.skipif(
    os.environ.get(GATE_VAR) != "1",
    reason=f"the winter-binding tier is local-only; set {GATE_VAR}=1 (see mise run winter-binding)",
)


def winter_source() -> Path | None:
    """A local winter workspace to clone: ``$BLIZZARD_MOCK_WINTER_SOURCE``'s, else the nearest enclosing one."""
    explicit = os.environ.get(SOURCE_VAR)
    start = Path(explicit).resolve() if explicit else Path(__file__).resolve().parents[2]
    for directory in [start, *start.parents]:
        if (directory / ".winter" / "config.toml").is_file() and (directory / "tools" / "winter-cli").is_dir():
            return directory
    return None


def require_winter_source() -> Path:
    """The discovered winter source, or skip naming the variable that points at one."""
    source = winter_source()
    if source is None:
        pytest.skip(f"no local winter source found; set {SOURCE_VAR}")
    return source

"""Names the unit and component ``test_flag_registry`` modules share."""

from __future__ import annotations

from blizzard.foundation.flags import FlagRegistry

_TOKEN = {"BZ_HUB_TOKEN_RUNNER_LOCAL": "tok"}

#: One live flag of each scope, and a tombstone.
_FIXTURE = """
[flag.hub-thing]
owner = "epic:release-engineering/dark-launch"
summary = "A hub capability"
scope = "hub"
default = "off"

[flag.runner-thing]
owner = "initiative:a-runner-thing"
summary = "A runner capability"
scope = "runner"
default = "on"

[flag.fleet-thing]
owner = "epic:ui-toolkit"
summary = "A capability hub and runners agree on"
scope = "fleet"
default = "off"

[retired]
old-thing = "fleet"
old-hub-thing = "hub"
"""


def _fixture() -> FlagRegistry:  # pyright: ignore[reportUnusedFunction]
    return FlagRegistry.parse(_FIXTURE, "fixture")

"""The hub's version window bound at the app boundary, as the runners domain's ``IVersionWindow``."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import collaborator
from blizzard.hub.domain.runners.version import VersionJudgement, VersionRefusal
from blizzard.wire.version_window import VersionWindow, admits


@collaborator
@dataclass(frozen=True)
class HubVersionWindow:
    """Judges runner versions for a hub at ``hub_version`` under ``window``."""

    hub_version: str
    window: VersionWindow

    def judge(self, version: str | None) -> VersionJudgement:
        admission = admits(self.hub_version, version, self.window)
        detail = admission.detail()
        return VersionJudgement(
            reported_version=admission.runner_version,
            refusal=None if admission.reason is None or detail is None else VersionRefusal(admission.reason, detail),
            hub_version=admission.hub_version,
            floor=admission.floor,
            ceiling=admission.ceiling,
        )

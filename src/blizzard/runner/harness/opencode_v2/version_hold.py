"""The major version a runner holds its OpenCode 2 binding to for the rest of its process."""

from __future__ import annotations

from blizzard.runner.harness.admission import version_admitted
from blizzard.runner.harness.opencode_v2.version import ADMITTED_OPENCODE_V2_RANGE, normalize_opencode_v2_version


def admits_opencode_v2_version(raw: str | None) -> bool:
    """Whether ``raw`` (a raw or bare version) is inside OpenCode 2's admitted range."""
    version = normalize_opencode_v2_version(raw)
    return version is not None and version_admitted(version, ADMITTED_OPENCODE_V2_RANGE)


class VersionHold:
    """The first service version observed outside OpenCode 2's range, kept until the runner restarts: a binding
    composed for one major version never drives another, even after the service returns to it. The adapter and the
    health probe share one hold, so a launch refused for it and the capability's availability agree."""

    def __init__(self) -> None:
        self._foreign: str | None = None

    def observe(self, version: str | None) -> None:
        """Hold ``version`` against the binding when it is the first one seen outside the range; one that is not a
        version at all, or ``None`` (nothing answered), holds nothing."""
        bare = normalize_opencode_v2_version(version)
        if bare is not None and self._foreign is None and not version_admitted(bare, ADMITTED_OPENCODE_V2_RANGE):
            self._foreign = bare

    @property
    def foreign(self) -> str | None:
        """The out-of-range version held, or ``None`` while every observed version was inside the range."""
        return self._foreign


__all__ = ["VersionHold", "admits_opencode_v2_version"]

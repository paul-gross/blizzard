"""The host hub client's configuration and late-bound report inputs."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from blizzard.foundation.roles import adapter_model
from blizzard.runner.hub.client import HostReport
from blizzard.runner.hub.host_registration import IStandingRunner


@adapter_model
@dataclass(frozen=True)
class HostHubOptions:
    url: str
    token: str = field(repr=False)
    report: Callable[[], HostReport]
    runners: Callable[[], Sequence[IStandingRunner]]

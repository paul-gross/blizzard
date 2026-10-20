"""The host's own pass: the work the daemon does for every runner it hosts, off any runner's tick.

``runner host`` runs it on a :class:`~blizzard.runner.loop.periodic_pass_driver.PeriodicPassDriver` of its own, so a
step blocked on a vendor CLI holds neither a runner's tick nor another runner's."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.runner.hub.host_registration import HostRegistration
from blizzard.runner.lifecycle.host_lane_drain import HostLaneDrain
from blizzard.runner.usage.credential_renewal import CredentialRenewalPass
from blizzard.runner.usage.external_usage_sample import ExternalUsageSamplePass


@dataclass(frozen=True)
class HostTick:
    """One pass over the host-scoped steps, in this order: the host's registration, absent on a host with no host
    token; the credential-renewal pass, absent when no provider binds a renewer — before sampling, so a slow vendor
    endpoint never delays it; the usage-sampling pass, absent when the host declares no subscription; then the host's
    lane drain, absent with the registration — after sampling, so a sample buffered this pass leaves in it."""

    registration: HostRegistration | None = None
    external_usage_sample: ExternalUsageSamplePass | None = None
    lane_drain: HostLaneDrain | None = None
    credential_renewal: CredentialRenewalPass | None = None

    def report(self) -> None:
        """The registration step alone — what the host runs once before it starts its runners' ticks."""
        if self.registration is not None:
            self.registration.run()

    def run(self) -> None:
        self.report()
        if self.credential_renewal is not None:
            self.credential_renewal.run()
        if self.external_usage_sample is not None:
            self.external_usage_sample.run()
        if self.lane_drain is not None:
            self.lane_drain.run()

"""The host's own pass: the work the daemon does for every runner it hosts, off any runner's tick.

``runner host`` runs it on a :class:`~blizzard.runner.usage.periodic_pass_driver.PeriodicPassDriver` of its own, so a
step blocked on a vendor CLI holds neither a runner's tick nor another runner's."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.runner.usage.credential_renewal import CredentialRenewalPass
from blizzard.runner.usage.external_usage_sample import ExternalUsageSamplePass


@dataclass(frozen=True)
class HostTick:
    """One pass over the host-scoped steps: the usage-sampling pass, absent when the host declares no subscription,
    then the credential-renewal pass, absent when no provider binds a renewer."""

    external_usage_sample: ExternalUsageSamplePass | None = None
    credential_renewal: CredentialRenewalPass | None = None

    def run(self) -> None:
        if self.external_usage_sample is not None:
            self.external_usage_sample.run()
        if self.credential_renewal is not None:
            self.credential_renewal.run()

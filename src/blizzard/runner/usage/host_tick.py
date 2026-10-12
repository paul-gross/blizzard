"""The host's own pass: the work the daemon does for every runner it hosts, off any runner's tick.

``runner host`` runs it on a :class:`~blizzard.runner.usage.periodic_pass_driver.PeriodicPassDriver` of its own, so a
step blocked on a vendor CLI holds neither a runner's tick nor another runner's."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.runner.usage.credential_renewal import CredentialRenewalPass


@dataclass(frozen=True)
class HostTick:
    """One pass over the host-scoped steps; today the credential-renewal pass, absent when no provider binds a
    renewer."""

    credential_renewal: CredentialRenewalPass | None = None

    def run(self) -> None:
        if self.credential_renewal is not None:
            self.credential_renewal.run()

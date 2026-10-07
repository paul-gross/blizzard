"""The credential-renewal pass — every declared subscription whose provider binds a renewer,
renewed on that slug's own cadence, off the reconciliation tick.

Directly callable (``bzh:steppable-loop``); ``runner host`` runs it on its own
:class:`~blizzard.runner.usage.periodic_pass_driver.PeriodicPassDriver`, so a renewal blocked on
the vendor CLI never holds the tick (``bzh:lane-contract`` clause 5)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.subscriptions.credential_renewer import ICredentialRenewer
from blizzard.runner.subscriptions.subscription_sampler import sample_due
from blizzard.runner.usage.repository import IWriteCredentialRenewalRepository

__all__ = ["CredentialRenewalPass", "RenewableSubscription"]

_log = get_logger("blizzard.runner.subscriptions")


@dataclass(frozen=True)
class RenewableSubscription:
    """One declared subscription whose provider binds a renewer, with the cadence it renews on."""

    slug: str
    sample_interval_seconds: int
    renewer: ICredentialRenewer


class CredentialRenewalPass:
    """Per slug, in order: cadence gate on the newest claim, due check, claim, renewal, outcome.
    A failed claim write fires no renewal; a failed outcome write leaves the claim anchoring
    the cadence, so nothing repeats inside it. One slug's failure never skips the next."""

    def __init__(
        self,
        *,
        subscriptions: Sequence[RenewableSubscription],
        renewals: IWriteCredentialRenewalRepository,
        clock: IClock,
    ) -> None:
        self._subscriptions = tuple(subscriptions)
        self._renewals = renewals
        self._clock = clock

    def run(self) -> None:
        """One pass over every renewable subscription."""
        for subscription in self._subscriptions:
            try:
                self._renew_one(subscription)
            except Exception as exc:  # one slug's failure must not skip the next
                _log.warning("credential renewal pass failed", slug=subscription.slug, detail=str(exc))

    def _renew_one(self, subscription: RenewableSubscription) -> None:
        anchor = self._renewals.last_credential_renewal_claim_at(subscription.slug)
        if not sample_due(anchor, self._clock.now(), subscription.sample_interval_seconds):
            return
        if not subscription.renewer.renewal_due():
            return
        claim_id = self._renewals.claim_credential_renewal(slug=subscription.slug, claimed_at=self._clock.now())
        outcome = subscription.renewer.renew()
        self._renewals.record_credential_renewal_outcome(
            claim_id=claim_id, outcome=outcome, recorded_at=self._clock.now()
        )

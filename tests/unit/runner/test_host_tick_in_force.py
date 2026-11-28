"""The host tick and its served subscriptions move as one process-lifetime value."""

from typing import cast

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.registry.interface import port
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.foundation.registry.internal.translation import HOST_LIFETIMES, build_container
from blizzard.foundation.registry.scopes import ScopeValues, supply
from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.hosting.retirement import RetirementNotices
from blizzard.runner.loop.host_tick import HostTick, HostTickInForce, register_host_tick
from blizzard.runner.loop_wiring import host_pass_stepper
from blizzard.runner.usage.credential_renewal import CredentialRenewalPass


class _Count:
    def __init__(self) -> None:
        self.passes = 0

    def run(self) -> None:
        self.passes += 1


def test_process_scope_supplies_one_holder_without_registering_its_tick() -> None:
    initial = HostTick()
    original = (SubscriptionDeclaration("team", "Team", "anthropic"),)
    holder = HostTickInForce(initial, original)
    registry = RecordingRegistry()
    register_host_tick(registry, HostTickInForce[SubscriptionDeclaration])
    owner = build_container(
        registry,
        HOST_LIFETIMES,
        owner="host",
        values=ScopeValues((supply(port(HostTickInForce[SubscriptionDeclaration]), holder),)),
    )
    try:
        assert owner.resolver.get(HostTickInForce[SubscriptionDeclaration]) is holder
        replacement = HostTick()
        holder.hold(replacement, ())
        assert owner.resolver.get(HostTickInForce[SubscriptionDeclaration]).snapshot() == (replacement, ())
        assert initial is not replacement
        assert all(reg.key is not HostTick for reg in registry.entries)
    finally:
        owner.close()


def test_stepper_reads_replaced_tick_on_each_pass_without_records_or_reconcile() -> None:
    first, second = _Count(), _Count()
    holder: HostTickInForce[SubscriptionDeclaration] = HostTickInForce(
        HostTick(credential_renewal=cast(CredentialRenewalPass, first)), ()
    )
    stepper = host_pass_stepper(
        holder, clock=SystemClock(), settings=None, records=None, retirements=RetirementNotices()
    )
    stepper.request()
    holder.hold(HostTick(credential_renewal=cast(CredentialRenewalPass, second)), ())
    stepper.request()
    assert (first.passes, second.passes) == (1, 1)

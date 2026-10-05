"""The provider-overload classification value.

A single fact an adapter translates from its own harness's raw exit signal, never a
decision (``bzh:deterministic-shell``): the loop is what backs a lease off from it, the
same shape :mod:`blizzard.runner.harness.usage`'s ``UsageLimit`` already established for a
sibling exit reason."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model

__all__ = ["ProviderOverload"]


@domain_model
@dataclass(frozen=True)
class ProviderOverload:
    """One invocation classified as exited on a provider-side overload (529) rather than
    completing — the harness's own report, carried through for the runner log at the point
    a backoff engages."""

    detail: str

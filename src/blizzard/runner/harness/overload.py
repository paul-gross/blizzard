"""The provider-overload classification value — a fact translated from a harness's raw exit
signal, never a decision (``bzh:deterministic-shell``)."""

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

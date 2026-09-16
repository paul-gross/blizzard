"""This runner's own capability snapshot (blizzard#433) — shared by every outbound call
that carries one: the registration push (``steps.py``'s ``Pull._sync_registry``) and the
matched fleet peek (``claim.py``'s ``ReadyQueue.peeked``). A free function rather than a
method on either caller's own module, since ``steps.py`` imports ``claim.py`` — a method
on one would make the other's use of it circular."""

from __future__ import annotations

from blizzard.runner.harness.registry import IHarnessRegistry
from blizzard.wire.runner import RunnerCapability


def capability_snapshot(harnesses: IHarnessRegistry) -> tuple[RunnerCapability, ...]:
    """One entry per known harness binding, each carrying the tier ids its adapter can
    resolve and its observed version (``None`` when the binding exposes none). The FIRST
    entry — today's only one, ``known_harnesses`` being single-entry — is marked
    ``default``: the runner's existing default harness, with no separate config key of
    its own."""
    snapshot: list[RunnerCapability] = []
    for index, harness_id in enumerate(harnesses.known_harnesses):
        adapter = harnesses.adapter(harness_id)
        snapshot.append(
            RunnerCapability(
                harness_id=harness_id,
                version=adapter.observe_version(),
                tiers=list(adapter.resolvable_tier_ids()),
                default=index == 0,
            )
        )
    return tuple(snapshot)

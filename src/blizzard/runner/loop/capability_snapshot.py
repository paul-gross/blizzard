"""This runner's own capability snapshot (blizzard#433) — shared by every outbound call
that carries one: the registration push (``steps.py``'s ``Pull._sync_registry``) and the
matched fleet peek (``claim.py``'s ``ReadyQueue.peeked``). A free function rather than a
method on either caller's own module, since ``steps.py`` imports ``claim.py`` — a method
on one would make the other's use of it circular."""

from __future__ import annotations

from dataclasses import dataclass, field

from blizzard.runner.harness.registry import IHarnessRegistry
from blizzard.wire.runner import RunnerCapability


def capability_snapshot(harnesses: IHarnessRegistry) -> tuple[RunnerCapability, ...]:
    """One entry per known harness binding, each carrying the tier ids its adapter can
    resolve and its observed version (``None`` when the binding exposes none). The FIRST
    entry is marked ``default``: the runner's existing default harness, with no separate
    config key of its own — so with several bindings known (Claude Code and OpenCode both
    are), it is the registry's own binding order that decides which one that is."""
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


@dataclass
class TickCapabilities:
    """One tick's own snapshot, built at most once — the per-tick memo shape
    ``chunk_status_cache`` already gives chunk reads. Every binding's ``observe_version``
    shells out to its own binary, so a snapshot rebuilt per registration and again per
    claim attempt pays that spawn again each time; a whole tick now pays it once."""

    harnesses: IHarnessRegistry
    _snapshot: tuple[RunnerCapability, ...] | None = field(default=None, compare=False)

    def get(self) -> tuple[RunnerCapability, ...]:
        if self._snapshot is None:
            self._snapshot = capability_snapshot(self.harnesses)
        return self._snapshot

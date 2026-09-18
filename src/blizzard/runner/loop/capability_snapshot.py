"""This runner's own capability snapshot (blizzard#433) — shared by every outbound call
that carries one: the registration push (``steps.py``'s ``Pull._sync_registry``) and the
matched fleet peek (``claim.py``'s ``ReadyQueue.peeked``). A free function rather than a
method on either caller's own module, since ``steps.py`` imports ``claim.py`` — a method
on one would make the other's use of it circular."""

from __future__ import annotations

from dataclasses import dataclass, field

from blizzard.runner.harness.registry import IHarnessRegistry
from blizzard.wire.runner import RunnerCapability


def default_harness_id(harnesses: IHarnessRegistry) -> str | None:
    """The runner's own default harness (blizzard#433) — the registry's own binding order
    decides which one that is, with no separate config key. ``None`` only for the legacy
    no-bindings registry some tests construct. The one place this is decided; both
    ``capability_snapshot`` and ``spawn.py``'s no-``session_harnesses`` fallback defer here."""
    known = harnesses.known_harnesses
    return known[0] if known else None


def capability_snapshot(harnesses: IHarnessRegistry) -> tuple[RunnerCapability, ...]:
    """One entry per known harness binding, each carrying the tier ids its adapter can
    resolve and its observed version (``None`` when the binding exposes none). The entry
    matching :func:`default_harness_id` is marked ``default``."""
    default_id = default_harness_id(harnesses)
    snapshot: list[RunnerCapability] = []
    for harness_id in harnesses.known_harnesses:
        adapter = harnesses.adapter(harness_id)
        snapshot.append(
            RunnerCapability(
                harness_id=harness_id,
                version=adapter.observe_version(),
                tiers=list(adapter.resolvable_tier_ids()),
                default=harness_id == default_id,
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

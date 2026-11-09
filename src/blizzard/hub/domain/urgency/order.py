"""A tenant's urgencies in order, most urgent first.

A place is an append-only position fact on an urgency, the newest per urgency winning (``bzh:facts-not-status``); a
position is a float, lower more urgent, and ties break on the slug. A retired urgency keeps its standing fact, so an
urgency revived returns to the place it left. Only a change of place appends a fact, and a position fact is not a
revision of the urgency it places. The order's sequence is the id of its newest fact, so a write planned on an order is
conditioned on it (``bzh:write-reestablishes-read``); a reorder that moves anything also appends one ``urgency_order``
change row naming the live order before and after, by slug."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, ConfigChange, FieldChange, RecordKind
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.kernel.float_slots import land_after
from blizzard.hub.domain.urgency.model import UrgencyId, UrgencyRetired, UrgencyUnknown

#: Where the first urgency of a tenant sits, and where a renumbering starts.
FIRST_POSITION = 0.0

#: The field an order's change row names.
ORDER_FIELD = "order"


class UrgencyOrderIncomplete(ConfigFieldError):
    """A whole order leaves a live urgency out, names a retired one, or names one twice."""

    code = "urgency_order_incomplete"

    def __init__(self, message: str) -> None:
        super().__init__(ORDER_FIELD, message)


class UrgencyOrderConflict(Exception):
    """A position write was planned on a sequence a competitor's write has since moved past."""

    code = "urgency_order_conflict"

    def __init__(self, *, expected: int) -> None:
        super().__init__("the urgency order changed after it was read")
        self.expected = expected


@domain_model
@dataclass(frozen=True)
class Placed:
    """One urgency at its place: its id, slug, newest position, and whether it is retired."""

    urgency_id: UrgencyId
    slug: str
    position: float
    retired: bool = False


@domain_model
@dataclass(frozen=True)
class PositionFact:
    """One position to append for an urgency."""

    urgency_id: UrgencyId
    position: float


def in_order(placed: Iterable[Placed]) -> list[Placed]:
    """``placed`` most urgent first: by position, ties on the slug."""
    return sorted(placed, key=lambda p: (p.position, p.slug))


@domain_model
@dataclass(frozen=True)
class UrgencyOrder:
    """Every urgency of a tenant at its place, retired ones included, in order, and the order's sequence — the id of
    its newest position fact, ``0`` when it holds none."""

    placed: tuple[Placed, ...]
    seq: int

    @classmethod
    def of(cls, placed: Iterable[Placed], seq: int) -> UrgencyOrder:
        return cls(tuple(in_order(placed)), seq)

    def live(self) -> tuple[Placed, ...]:
        """The live urgencies, in order."""
        return tuple(p for p in self.placed if not p.retired)

    def live_slugs(self) -> tuple[str, ...]:
        return tuple(p.slug for p in self.live())

    def after(self, facts: Sequence[PositionFact]) -> UrgencyOrder:
        """This order once ``facts`` land, its sequence unknown until they do."""
        moved = {fact.urgency_id: fact.position for fact in facts}
        return UrgencyOrder.of(
            (replace(p, position=moved[p.urgency_id]) if p.urgency_id in moved else p for p in self.placed), self.seq
        )

    def _by_id(self) -> dict[UrgencyId, Placed]:
        return {p.urgency_id: p for p in self.placed}

    def landing(self, after: UrgencyId | None, *, last: bool) -> tuple[float, tuple[PositionFact, ...]]:
        """Where a new urgency lands — at the least urgent end when ``last``, else just after ``after``, first when
        ``after`` is ``None`` — and the facts renumbering the set first when the gap there is spent. ``after`` must be
        live (:class:`UrgencyUnknown` or :class:`UrgencyRetired` on ``after``)."""
        if last:
            return (self.placed[-1].position + 1.0 if self.placed else FIRST_POSITION), ()
        index = None if after is None else self._live_index(after, list(self.placed))
        slot = land_after([p.position for p in self.placed], index, start=FIRST_POSITION)
        renumber = tuple(
            PositionFact(p.urgency_id, position)
            for p, position in zip(self.placed, slot.renumbered, strict=False)
            if p.position != position
        )
        return slot.position, renumber

    def _live_index(self, urgency_id: UrgencyId, among: list[Placed]) -> int:
        _live("after", urgency_id, self._by_id().get(urgency_id))
        return next(i for i, p in enumerate(among) if p.urgency_id == urgency_id)

    def whole(self, ordered: Sequence[UrgencyId]) -> tuple[PositionFact, ...]:
        """The facts that set the live order to ``ordered``, which names every live urgency once: none when the live
        order already is ``ordered``. :class:`UrgencyOrderIncomplete` when it leaves one out, names a retired one, or
        names one twice; :class:`UrgencyUnknown` for an id the tenant does not hold."""
        by_id = self._by_id()
        seen: set[UrgencyId] = set()
        for urgency_id in ordered:
            placed = by_id.get(urgency_id)
            if placed is None:
                raise UrgencyUnknown(ORDER_FIELD, urgency_id)
            if placed.retired:
                raise UrgencyOrderIncomplete(f"urgency {placed.slug} is retired")
            if urgency_id in seen:
                raise UrgencyOrderIncomplete(f"urgency {placed.slug} is named twice")
            seen.add(urgency_id)
        left_out = [p.slug for p in self.live() if p.urgency_id not in seen]
        if left_out:
            raise UrgencyOrderIncomplete(f"the order leaves out {', '.join(left_out)}")
        if tuple(p.urgency_id for p in self.live()) == tuple(ordered):
            return ()
        wanted = {urgency_id: FIRST_POSITION + index for index, urgency_id in enumerate(ordered)}
        return tuple(
            PositionFact(urgency_id, position)
            for urgency_id, position in wanted.items()
            if by_id[urgency_id].position != position
        )

    def move(self, urgency_id: UrgencyId, *, after: UrgencyId | None) -> tuple[PositionFact, ...]:
        """The facts that move the live ``urgency_id`` to just after the live ``after`` — first when ``after`` is
        ``None``: none when it already stands there. :class:`ConfigFieldError` for a move after itself."""
        by_id = self._by_id()
        moving = _live("urgency", urgency_id, by_id.get(urgency_id))
        if after == urgency_id:
            raise ConfigFieldError("after", "an urgency cannot move after itself")
        others = [p for p in self.placed if p.urgency_id != urgency_id]
        index = None if after is None else self._live_index(after, others)
        slot = land_after([p.position for p in others], index, start=FIRST_POSITION)
        facts = [
            PositionFact(p.urgency_id, position)
            for p, position in zip(others, slot.renumbered, strict=False)
            if p.position != position
        ]
        if by_id[moving.urgency_id].position != slot.position:
            facts.append(PositionFact(urgency_id, slot.position))
        if self.after(facts).live_slugs() == self.live_slugs():
            return ()
        return tuple(facts)


def _live(field: str, key: str, placed: Placed | None) -> Placed:
    """``placed`` — what ``key`` on ``field`` named — when it is live; :class:`UrgencyUnknown` or
    :class:`UrgencyRetired` otherwise."""
    if placed is None:
        raise UrgencyUnknown(field, key)
    if placed.retired:
        raise UrgencyRetired(field, placed.slug)
    return placed


def order_change(
    ctx: ChangeContext, tenant_id: str, before: UrgencyOrder, after: UrgencyOrder, *, at: datetime
) -> ConfigChange:
    """The ``urgency_order`` change row of a reorder, keyed by the tenant: the live order before and after, by slug.
    Its revision is the order's sequence once the facts land, which the store fills in as it appends them."""
    diff = (FieldChange(ORDER_FIELD, list(before.live_slugs()), list(after.live_slugs())),)
    return ConfigChange.of(ctx, RecordKind.URGENCY_ORDER, tenant_id, before.seq, ChangeOp.EDIT, diff, at)

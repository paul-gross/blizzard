"""The urgency verbs — create, list, show, edit, retire, enable — the set's two reorders, the tenant's settings, and the
tenant's default read for a mint.

Each service loads what it needs, hands it to the model's pure functions, and passes the result to the repository
(``bzh:domain-orchestration-split``). A write that lands a position, changes the default, or retires an urgency is one
unit of work declaring its keyed locks once and re-reading under them what it decides on
(``bzh:write-reestablishes-read``); an edit and an enable are compare-and-set on ``revision`` and lock nothing. The
caller publishes what a write moved once it commits (``bzh:publish-after-commit``)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import ChangeContext
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.kernel.transactions import IHubTransactions, Transaction
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType
from blizzard.hub.domain.projects.color import ColorPick
from blizzard.hub.domain.urgency.mark import Icon
from blizzard.hub.domain.urgency.model import (
    Urgency,
    UrgencyEdit,
    UrgencyId,
    UrgencyShape,
    UrgencySlug,
    UrgencyUnknown,
    moved_diff,
    require_live,
    require_retirable,
    require_slug_free,
)
from blizzard.hub.domain.urgency.order import PositionFact, UrgencyOrder, order_change
from blizzard.hub.domain.urgency.ports import (
    IReadTenantSettings,
    IReadUrgencyChunks,
    IReadUrgencyOrder,
    IReadUrgencyRepository,
    IWriteTenantSettings,
    IWriteUrgencyChunks,
    IWriteUrgencyOrder,
    IWriteUrgencyRepository,
    tenant_settings_lock,
    urgency_lock,
    urgency_order_lock,
)
from blizzard.hub.domain.urgency.settings import DEFAULT_URGENCY_FIELD, TenantSettings


class TenantSettingsMissing(LookupError):
    """The tenant holds no settings — only a tenant whose creation has not yet seeded them."""

    def __init__(self) -> None:
        super().__init__("the tenant's settings are not seeded")


def resolve(urgencies: IReadUrgencyRepository, key: str, *, transaction: Transaction | None = None) -> Urgency | None:
    """The urgency ``key`` names — by id, or by slug — or ``None``. The slug grammar admits no ``_``, so an id never
    reads as a slug."""
    if UrgencySlug.of(key) is not None:
        return urgencies.get_by_slug(key, transaction=transaction)
    return urgencies.get(UrgencyId(key), transaction=transaction)


def _settings(settings: IReadTenantSettings, *, transaction: Transaction | None = None) -> TenantSettings:
    held = settings.get(transaction=transaction)
    if held is None:
        raise TenantSettingsMissing
    return held


@domain_model
@dataclass(frozen=True)
class UrgencyView:
    """An urgency as a read shows it: its place in the live order (``1`` the most urgent; ``None`` once retired),
    whether it is the tenant's default, and how many open chunks carry it."""

    urgency: Urgency
    place: int | None
    default: bool
    open_chunks: int


@domain_model
@dataclass(frozen=True)
class SettingsView:
    """The tenant's settings with the urgency they name as the default."""

    settings: TenantSettings
    default: Urgency


class UrgencyReading:
    """List and show urgencies: one read of the order, the settings, and one grouped count of open chunks beside the
    urgencies, never one read per urgency (``bzh:read-cost``)."""

    def __init__(
        self,
        *,
        urgencies: IReadUrgencyRepository,
        order: IReadUrgencyOrder,
        settings: IReadTenantSettings,
        chunks: IReadUrgencyChunks,
    ) -> None:
        self._urgencies = urgencies
        self._order = order
        self._settings = settings
        self._chunks = chunks

    def resolve(self, key: str) -> Urgency | None:
        return resolve(self._urgencies, key)

    def list(self, *, include_retired: bool = False) -> list[UrgencyView]:
        """The live urgencies in order, most urgent first; with ``include_retired``, the retired ones too, each at
        its standing place."""
        order = self._order.order()
        by_id = {u.urgency_id: u for u in self._urgencies.list_all()}
        shown = [by_id[p.urgency_id] for p in order.placed if include_retired or not p.retired]
        return self._views(shown, order)

    def view(self, urgency: Urgency) -> UrgencyView:
        (view,) = self._views([urgency], self._order.order())
        return view

    def icon(self, urgency: Urgency) -> Icon | None:
        return self._urgencies.icon(urgency.urgency_id)

    def _views(self, shown: Sequence[Urgency], order: UrgencyOrder) -> list[UrgencyView]:
        default_id = _settings(self._settings).default_urgency_id
        places = {p.urgency_id: index for index, p in enumerate(order.live, start=1)}
        counts = self._chunks.open_counts([u.urgency_id for u in shown])
        return [
            UrgencyView(
                urgency=u,
                place=places.get(u.urgency_id),
                default=u.urgency_id == default_id,
                open_chunks=counts.get(u.urgency_id, 0),
            )
            for u in shown
        ]


class UrgencyAuthoring:
    """Create and edit an urgency. A create lands a position, so it takes the set's lock and re-reads the order and
    the slug under it; an edit is a compare-and-set on ``revision``."""

    def __init__(
        self,
        *,
        urgencies: IWriteUrgencyRepository,
        order: IWriteUrgencyOrder,
        transactions: IHubTransactions,
        pick: ColorPick,
        clock: IClock,
    ) -> None:
        self._urgencies = urgencies
        self._order = order
        self._transactions = transactions
        self._pick = pick
        self._clock = clock

    def create(self, shape: UrgencyShape, ctx: ChangeContext, *, after: str | None | UnsetType = UNSET) -> Urgency:
        """Create an urgency at the least urgent end, or just after the live urgency ``after`` names — first under
        ``None``. A create stating no color takes the palette color the live urgencies use least."""
        at = self._clock.now()
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[urgency_order_lock()])
            held = self._urgencies.list_all(transaction=locked)
            urgency, mark, change = Urgency.new(
                shape,
                ctx,
                siblings=lambda: [u.color for u in held if not u.retired],
                pick=self._pick,
                at=at,
            )
            require_slug_free(urgency.slug, next((u for u in held if u.slug == urgency.slug), None))
            order = self._order.order(transaction=locked)
            anchor = None if after is UNSET or after is None else _anchor(held, after)
            position, renumber = order.landing(anchor, last=after is UNSET)
            self._order.record_positions(
                renumber, from_seq=order.seq, at=at, by=ctx.actor, change=None, transaction=locked
            )
            return self._urgencies.create(urgency, mark=mark, position=position, change=change, transaction=locked)

    def edit(self, urgency: Urgency, edit: UrgencyEdit, ctx: ChangeContext, *, if_match: int | None) -> Urgency:
        """Apply a sparse edit; one that changes nothing writes nothing."""
        decided = urgency.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return urgency
        edited, mark, change = decided
        return self._urgencies.update(edited, mark=mark, from_revision=urgency.revision, change=change)


def _anchor(held: Sequence[Urgency], key: str) -> UrgencyId:
    """The id of the urgency ``key`` names among ``held``, which the order then confirms live; an unknown key passes
    through so the order refuses it naming ``after``."""
    found = next((u for u in held if key in (u.urgency_id, u.slug)), None)
    return UrgencyId(key) if found is None else found.urgency_id


@domain_model
@dataclass(frozen=True)
class Retired:
    """A retire's outcome: the urgency as it stands, and the open chunks it moved — none when it moved nothing or
    the urgency was already retired."""

    urgency: Urgency
    moved: tuple[str, ...]


class UrgencyLifecycle:
    """Retire and enable an urgency. A retire is one unit locking the settings, the retiring urgency and the urgency
    its open work moves to; under them it re-reads the default, the urgency, its open chunks and ``move_to``, clears
    the refusals in order, and writes the retired fact, the move and the change together. No chunk is locked: every
    write setting a chunk to an urgency takes that urgency's lock and confirms it live. Enable moves no chunk back."""

    def __init__(
        self,
        *,
        urgencies: IWriteUrgencyRepository,
        settings: IReadTenantSettings,
        chunks: IWriteUrgencyChunks,
        transactions: IHubTransactions,
        clock: IClock,
    ) -> None:
        self._urgencies = urgencies
        self._settings = settings
        self._chunks = chunks
        self._transactions = transactions
        self._clock = clock

    def retire(
        self,
        urgency: Urgency,
        ctx: ChangeContext,
        *,
        move_to: str | None,
        may_control: bool,
        if_match: int | None,
    ) -> Retired:
        """Retire ``urgency``, moving its open chunks to the urgency ``move_to`` names. Retiring a retired urgency
        writes nothing. ``may_control`` says whether the writer may edit chunks, which a retire moving any needs."""
        named = None if move_to is None else resolve(self._urgencies, move_to)
        keys = [tenant_settings_lock(), urgency_lock(urgency.urgency_id)]
        if named is not None and named.urgency_id != urgency.urgency_id:
            keys.append(urgency_lock(named.urgency_id))
        at = self._clock.now()
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=keys)
            current = self._urgencies.get(urgency.urgency_id, transaction=locked)
            assert current is not None, "an urgency is never deleted"
            current.require_revision(if_match)
            if current.retired:
                return Retired(current, ())
            default_id = _settings(self._settings, transaction=locked).default_urgency_id
            open_chunks = self._chunks.open_chunks(current.urgency_id, transaction=locked)
            target = None if named is None else self._urgencies.get(named.urgency_id, transaction=locked)
            destination = require_retirable(
                current,
                default_id=default_id,
                open_chunks=len(open_chunks),
                move_to_key=move_to,
                move_to=target,
                may_control=may_control,
            )
            moved = (
                self._chunks.move(open_chunks, to=destination.urgency_id, transaction=locked)
                if destination is not None
                else 0
            )
            decided = current.set_retired(True, ctx, if_match=None, at=at, extra=moved_diff(destination, moved))
            assert decided is not None, "a live urgency always retires"
            retired, change = decided
            stored = self._urgencies.record_lifecycle(
                retired,
                retired=True,
                from_revision=current.revision,
                at=at,
                by=ctx.actor,
                change=change,
                transaction=locked,
            )
        return Retired(stored, tuple(open_chunks) if destination is not None else ())

    def enable(self, urgency: Urgency, ctx: ChangeContext, *, if_match: int | None) -> Urgency:
        """Bring a retired urgency back at its standing place; enabling a live one writes nothing."""
        at = self._clock.now()
        decided = urgency.set_retired(False, ctx, if_match=if_match, at=at)
        if decided is None:
            return urgency
        enabled, change = decided
        return self._urgencies.record_lifecycle(
            enabled, retired=False, from_revision=urgency.revision, at=at, by=ctx.actor, change=change
        )


class UrgencyOrdering:
    """The set's two reorders. Each takes the set's lock, re-reads the order under it, and appends the position facts
    and one ``urgency_order`` change row only when something moves."""

    def __init__(
        self,
        *,
        tenant_id: str,
        urgencies: IReadUrgencyRepository,
        order: IWriteUrgencyOrder,
        transactions: IHubTransactions,
        clock: IClock,
    ) -> None:
        self._tenant_id = tenant_id
        self._urgencies = urgencies
        self._order = order
        self._transactions = transactions
        self._clock = clock

    def set_order(self, keys: Sequence[str], ctx: ChangeContext) -> bool:
        """Set the whole live order from ``keys``, each an id or a slug, naming every live urgency once. Whether
        anything moved."""
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[urgency_order_lock()])
            ids = [self._id_of(key, "urgencies", locked) for key in keys]
            order = self._order.order(transaction=locked)
            return self._record(order, order.whole(ids), ctx, locked)

    def move(self, key: str, ctx: ChangeContext, *, after: str | None) -> bool:
        """Move the urgency ``key`` names to just after the one ``after`` names, first under ``None``. Whether
        anything moved."""
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[urgency_order_lock()])
            moving = self._id_of(key, "urgency", locked)
            anchor = None if after is None else self._id_of(after, "after", locked)
            order = self._order.order(transaction=locked)
            return self._record(order, order.move(moving, after=anchor), ctx, locked)

    def _id_of(self, key: str, field: str, transaction: Transaction) -> UrgencyId:
        """The id of the urgency ``key`` names; the order then refuses a retired one."""
        found = resolve(self._urgencies, key, transaction=transaction)
        if found is None:
            raise UrgencyUnknown(field, key)
        return found.urgency_id

    def _record(
        self, order: UrgencyOrder, facts: Sequence[PositionFact], ctx: ChangeContext, transaction: Transaction
    ) -> bool:
        if not facts:
            return False
        at = self._clock.now()
        change = order_change(ctx, self._tenant_id, order, order.after(facts), at=at)
        self._order.record_positions(
            facts, from_seq=order.seq, at=at, by=ctx.actor, change=change, transaction=transaction
        )
        return True


class TenantSettingsService:
    """Show and edit the tenant's settings. An edit naming a default takes the settings' lock and the named urgency's,
    and confirms under them that it is live, so the default never lands on an urgency being retired."""

    def __init__(
        self,
        *,
        settings: IWriteTenantSettings,
        urgencies: IReadUrgencyRepository,
        transactions: IHubTransactions,
        clock: IClock,
    ) -> None:
        self._settings = settings
        self._urgencies = urgencies
        self._transactions = transactions
        self._clock = clock

    def show(self) -> SettingsView:
        settings = _settings(self._settings)
        default = self._urgencies.get(settings.default_urgency_id)
        assert default is not None, "the default always names a stored urgency"
        return SettingsView(settings, default)

    def edit(
        self, ctx: ChangeContext, *, default_urgency: str | None | UnsetType, if_match: int | None
    ) -> SettingsView:
        """A sparse edit: an absent ``default_urgency`` leaves it, an explicit ``None`` is refused, and one naming an
        urgency the tenant does not hold or a retired one is refused. One that changes nothing writes nothing."""
        if default_urgency is UNSET:
            current = self.show()
            current.settings.require_revision(if_match)
            return current
        if default_urgency is None:
            raise ConfigFieldError(DEFAULT_URGENCY_FIELD, "must not be null")
        named = require_live(DEFAULT_URGENCY_FIELD, default_urgency, resolve(self._urgencies, default_urgency))
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[tenant_settings_lock(), urgency_lock(named.urgency_id)])
            settings = _settings(self._settings, transaction=locked)
            default = require_live(
                DEFAULT_URGENCY_FIELD,
                default_urgency,
                self._urgencies.get(named.urgency_id, transaction=locked),
            )
            held = self._urgencies.get(settings.default_urgency_id, transaction=locked)
            assert held is not None, "the default always names a stored urgency"
            decided = settings.point_at(default, held=held, ctx=ctx, if_match=if_match, at=self._clock.now())
            if decided is None:
                return SettingsView(settings, default)
            edited, change = decided
            stored = self._settings.update(edited, from_revision=settings.revision, change=change, transaction=locked)
        return SettingsView(stored, default)


class TenantDefault:
    """The tenant's default urgency for a mint. :meth:`lock_keys`, read before the mint's unit, names the locks the
    unit declares beside its own; :meth:`read`, under them, re-reads the default and confirms it live. The settings'
    lock holds the default still, since every change of the default and every retire takes it, so a default that
    moved between the two reads is still live."""

    def __init__(self, *, settings: IReadTenantSettings, urgencies: IReadUrgencyRepository) -> None:
        self._settings = settings
        self._urgencies = urgencies

    def read_id(self) -> UrgencyId:
        """The default's id as it stands before the mint's unit — what its locks are named from."""
        return _settings(self._settings).default_urgency_id

    def lock_keys(self) -> list[tuple[str, str]]:
        return default_lock_keys(self.read_id())

    def read(self, transaction: Transaction) -> Urgency:
        settings = _settings(self._settings, transaction=transaction)
        held = self._urgencies.get(settings.default_urgency_id, transaction=transaction)
        return require_live(DEFAULT_URGENCY_FIELD, settings.default_urgency_id, held)


def default_lock_keys(default_id: UrgencyId) -> list[tuple[str, str]]:
    """The keyed locks a mint writing the default ``default_id`` declares: the settings' and the urgency's."""
    return [tenant_settings_lock(), urgency_lock(default_id)]

"""The urgency verbs — create, list, show, edit, retire, enable — the set's two reorders, the tenant's settings, and the
tenant's default read for a mint.

Each service loads what it needs, hands it to the model's pure functions, and passes the result to the repository
(``bzh:domain-orchestration-split``). A write that lands a position, changes the default, or retires an urgency is one
unit of work declaring its keyed locks once and re-reading under them what it decides on
(``bzh:write-reestablishes-read``); an edit is a compare-and-set on ``revision`` and locks nothing. The edge
resolves each urgency a write names before calling in (``bzh:domain-takes-objects``), and the unit re-reads it by id.
The caller publishes what a write moved once it commits (``bzh:publish-after-commit``)."""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import ChangeContext
from blizzard.hub.domain.kernel.transactions import IHubTransactions, Transaction
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType
from blizzard.hub.domain.projects.color import ColorPick
from blizzard.hub.domain.urgency.chain import URGENCY_FIELD, ChainLinks, resolve_chain
from blizzard.hub.domain.urgency.mark import Icon
from blizzard.hub.domain.urgency.model import (
    MoveTo,
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
    IUrgencyReferenceClearing,
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

    def named(self, field: str, key: str) -> Urgency:
        """The urgency ``key`` on ``field`` names; :class:`UrgencyUnknown` when it names none."""
        found = self.resolve(key)
        if found is None:
            raise UrgencyUnknown(field, key)
        return found

    def live(self, field: str, key: str) -> Urgency:
        """The live urgency ``key`` on ``field`` names — a mint's stated urgency, checked before its unit;
        :class:`UrgencyUnknown` when it names none, :class:`UrgencyRetired` when it names a retired one."""
        return require_live(field, key, self.resolve(key))

    def list(self, *, include_retired: bool = False) -> list[UrgencyView]:
        """The live urgencies in order, most urgent first; with ``include_retired``, the retired ones too, each at
        its standing place."""
        order = self._order.order()
        return self._views(self._in(order, include_retired=include_retired), order)

    def ordered(self) -> list[Urgency]:
        """The live urgencies in order, most urgent first, without the counts and places a view carries."""
        return self._in(self._order.order(), include_retired=False)

    def _in(self, order: UrgencyOrder, *, include_retired: bool) -> list[Urgency]:
        by_id = {u.urgency_id: u for u in self._urgencies.list_all()}
        return [by_id[p.urgency_id] for p in order.placed if include_retired or not p.retired]

    def by_id(self) -> dict[UrgencyId, Urgency]:
        """Every urgency of the tenant by id, the retired ones included, in one read — what a response naming
        chunks' urgencies joins them to."""
        return {u.urgency_id: u for u in self._urgencies.list_all()}

    def view(self, urgency: Urgency) -> UrgencyView:
        (view,) = self._views([urgency], self._order.order())
        return view

    def icon(self, urgency: Urgency) -> Icon | None:
        return self._urgencies.icon(urgency.urgency_id)

    def icons(self) -> dict[UrgencyId, Icon]:
        """The icon bytes of every urgency carrying one — only the config document's export carries them."""
        return self._urgencies.icons()

    def _views(self, shown: Sequence[Urgency], order: UrgencyOrder) -> list[UrgencyView]:
        default_id = _settings(self._settings).default_urgency_id
        places = {p.urgency_id: index for index, p in enumerate(order.live(), start=1)}
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

    def create(self, shape: UrgencyShape, ctx: ChangeContext, *, after: Urgency | None | UnsetType = UNSET) -> Urgency:
        """Create an urgency at the least urgent end, or just after ``after``, which the order re-read under the lock
        confirms live — first under ``None``. A create stating no color takes the palette color the live urgencies use
        least."""
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
            anchor = None if after is UNSET or after is None else after.urgency_id
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


@domain_model
@dataclass(frozen=True)
class Retired:
    """A retire's outcome: the urgency as it stands, and the open chunks it moved — none when it moved nothing or
    the urgency was already retired."""

    urgency: Urgency
    moved: tuple[str, ...]


class UrgencyLifecycle:
    """Retire and enable an urgency, each under the set's lock. A retire locks its open chunks (read ahead), then the
    settings, the urgency and ``move_to``; re-reads all, refuses, then retires, moves, and clears ``references`` in
    one unit — rerunning wider if the re-read finds a chunk outside the locked set. An enable re-reads the urgency
    under its lock and restores neither chunks nor references."""

    def __init__(
        self,
        *,
        urgencies: IWriteUrgencyRepository,
        settings: IReadTenantSettings,
        chunks: IWriteUrgencyChunks,
        transactions: IHubTransactions,
        clock: IClock,
        references: Sequence[IUrgencyReferenceClearing] = (),
    ) -> None:
        self._urgencies = urgencies
        self._settings = settings
        self._chunks = chunks
        self._transactions = transactions
        self._clock = clock
        self._references = tuple(references)

    def retire(
        self,
        urgency: Urgency,
        ctx: ChangeContext,
        *,
        move_to: MoveTo | None,
        may_control: bool,
        if_match: int | None,
    ) -> Retired:
        """Retire ``urgency``, moving its open chunks to the urgency ``move_to`` names. Retiring a retired urgency
        writes nothing. ``may_control`` says whether the writer may edit chunks, which a retire moving any needs."""
        named = None if move_to is None else move_to.urgency
        keys = [tenant_settings_lock(), urgency_order_lock(), urgency_lock(urgency.urgency_id)]
        if named is not None and named.urgency_id != urgency.urgency_id:
            keys.append(urgency_lock(named.urgency_id))
        chunk_ids = frozenset(self._chunks.open_chunks(urgency.urgency_id))
        while True:
            outcome = self._retire_locked(
                urgency, ctx, keys, chunk_ids, move_to=move_to, may_control=may_control, if_match=if_match
            )
            if isinstance(outcome, Retired):
                return outcome
            chunk_ids = chunk_ids | outcome

    def _retire_locked(
        self,
        urgency: Urgency,
        ctx: ChangeContext,
        keys: Sequence[tuple[str, str]],
        chunk_ids: frozenset[str],
        *,
        move_to: MoveTo | None,
        may_control: bool,
        if_match: int | None,
    ) -> Retired | frozenset[str]:
        """One run of the retire's unit over ``chunk_ids``: the retire, or — writing nothing — the open chunks the
        re-read found outside the locked set."""
        named = None if move_to is None else move_to.urgency
        at = self._clock.now()
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(chunks=sorted(chunk_ids), keys=keys)
            current = self._urgencies.get(urgency.urgency_id, transaction=locked)
            assert current is not None, "an urgency is never deleted"
            current.require_revision(if_match)
            if not current.retires_to(True):
                return Retired(current, ())
            default_id = _settings(self._settings, transaction=locked).default_urgency_id
            open_chunks = self._chunks.open_chunks(current.urgency_id, transaction=locked)
            unlocked = frozenset(open_chunks) - chunk_ids
            if unlocked:
                return unlocked
            target = None if named is None else self._urgencies.get(named.urgency_id, transaction=locked)
            destination = require_retirable(
                current,
                default_id=default_id,
                open_chunks=len(open_chunks),
                move_to_key=None if move_to is None else move_to.key,
                move_to=target,
                may_control=may_control,
            )
            moved = (
                self._chunks.move(
                    open_chunks, from_id=current.urgency_id, to=destination.urgency_id, transaction=locked
                )
                if destination is not None
                else []
            )
            for references in self._references:
                references.clear(current, ctx, at=at, transaction=locked)
            decided = current.set_retired(True, ctx, if_match=None, at=at, extra=moved_diff(destination, len(moved)))
            assert decided is not None, "the model answered the no-op above"
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
        return Retired(stored, tuple(moved))

    def enable(self, urgency: Urgency, ctx: ChangeContext, *, if_match: int | None) -> Urgency:
        """Bring a retired urgency back at its standing place; enabling a live one writes nothing."""
        at = self._clock.now()
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[urgency_order_lock(), urgency_lock(urgency.urgency_id)])
            current = self._urgencies.get(urgency.urgency_id, transaction=locked)
            assert current is not None, "an urgency is never deleted"
            decided = current.set_retired(False, ctx, if_match=if_match, at=at)
            if decided is None:
                return current
            enabled, change = decided
            return self._urgencies.record_lifecycle(
                enabled,
                retired=False,
                from_revision=current.revision,
                at=at,
                by=ctx.actor,
                change=change,
                transaction=locked,
            )


class UrgencyOrdering:
    """The set's two reorders. Each takes the set's lock, re-reads the order under it, and appends the position facts
    and one ``urgency_order`` change row only when something moves."""

    def __init__(
        self,
        *,
        tenant_id: str,
        order: IWriteUrgencyOrder,
        transactions: IHubTransactions,
        clock: IClock,
    ) -> None:
        self._tenant_id = tenant_id
        self._order = order
        self._transactions = transactions
        self._clock = clock

    def set_order(self, urgencies: Sequence[Urgency], ctx: ChangeContext) -> bool:
        """Set the whole live order to ``urgencies``, naming every live urgency once; the order re-read under the lock
        refuses a retired one. Whether anything moved."""
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[urgency_order_lock()])
            order = self._order.order(transaction=locked)
            return self._record(order, order.whole([u.urgency_id for u in urgencies]), ctx, locked)

    def move(self, urgency: Urgency, ctx: ChangeContext, *, after: Urgency | None) -> bool:
        """Move ``urgency`` to just after ``after``, first under ``None``; the order re-read under the lock refuses a
        retired one. Whether anything moved."""
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[urgency_order_lock()])
            order = self._order.order(transaction=locked)
            anchor = None if after is None else after.urgency_id
            return self._record(order, order.move(urgency.urgency_id, after=anchor), ctx, locked)

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

    def edit(self, ctx: ChangeContext, *, default_urgency: Urgency | UnsetType, if_match: int | None) -> SettingsView:
        """A sparse edit: an absent ``default_urgency`` leaves it, and a retired one is refused. One that changes
        nothing writes nothing."""
        if default_urgency is UNSET:
            current = self.show()
            current.settings.require_revision(if_match)
            return current
        named = require_live(DEFAULT_URGENCY_FIELD, default_urgency.slug, default_urgency)
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[tenant_settings_lock(), urgency_lock(named.urgency_id)])
            settings = _settings(self._settings, transaction=locked)
            default = require_live(
                DEFAULT_URGENCY_FIELD, named.slug, self._urgencies.get(named.urgency_id, transaction=locked)
            )
            held = self._urgencies.get(settings.default_urgency_id, transaction=locked)
            assert held is not None, "the default always names a stored urgency"
            decided = settings.point_at(default, held=held, ctx=ctx, if_match=if_match, at=self._clock.now())
            if decided is None:
                return SettingsView(settings, default)
            edited, change = decided
            stored = self._settings.update(edited, from_revision=settings.revision, change=change, transaction=locked)
        return SettingsView(stored, default)


@domain_model
@dataclass(frozen=True)
class MintAhead:
    """A mint's urgency chain as read ahead of its unit: the id each link names, and the default's.
    :meth:`lock_keys` names the locks the unit declares beside its own — the settings' and every named urgency's."""

    default_id: UrgencyId
    links: ChainLinks[UrgencyId] = field(default_factory=ChainLinks[UrgencyId])

    def lock_keys(self) -> list[tuple[str, str]]:
        named = set(self.links.named()) - {self.default_id}
        return [*default_lock_keys(self.default_id), *sorted(urgency_lock(urgency_id) for urgency_id in named)]


class TenantDefault:
    """The mint's urgency seam (``bzh:write-reestablishes-read``): :meth:`ahead`, before the mint's unit, names the
    locks of the default and of every urgency a link names, retired or not; :meth:`resolve`, under them, re-reads
    each and resolves the chain (:func:`~blizzard.hub.domain.urgency.chain.resolve_chain`)."""

    def __init__(self, *, settings: IReadTenantSettings, urgencies: IReadUrgencyRepository) -> None:
        self._settings = settings
        self._urgencies = urgencies

    def ahead(
        self, *, stated: Urgency | None = None, chore: UrgencyId | None = None, routine: UrgencyId | None = None
    ) -> MintAhead:
        """The chain ahead of the unit: ``stated`` — the urgency the request names, which the edge resolved and
        confirmed live — and the ids a chore's override and a routine's default name."""
        return MintAhead(
            default_id=_settings(self._settings).default_urgency_id,
            links=ChainLinks(stated=None if stated is None else stated.urgency_id, chore=chore, routine=routine),
        )

    def resolve(self, ahead: MintAhead, transaction: Transaction) -> Urgency:
        """The urgency the chunk is minted with, read under the locks ``ahead`` named.
        :class:`~blizzard.hub.domain.urgency.model.UrgencyRetired` when the stated urgency was retired since."""
        settings = _settings(self._settings, transaction=transaction)
        default = require_live(
            DEFAULT_URGENCY_FIELD,
            settings.default_urgency_id,
            self._urgencies.get(settings.default_urgency_id, transaction=transaction),
        )
        links = ahead.links
        return resolve_chain(
            ChainLinks(
                stated=self._again(URGENCY_FIELD, links.stated, transaction),
                chore=self._again(URGENCY_FIELD, links.chore, transaction),
                routine=self._again(DEFAULT_URGENCY_FIELD, links.routine, transaction),
            ),
            default,
        )

    def _again(self, field: str, urgency_id: UrgencyId | None, transaction: Transaction) -> Urgency | None:
        return None if urgency_id is None else reread(self._urgencies, field, urgency_id, transaction=transaction)


def reread(
    urgencies: IReadUrgencyRepository, field: str, urgency_id: UrgencyId, *, transaction: Transaction
) -> Urgency:
    """The urgency ``urgency_id`` names, read again in ``transaction`` under its lock; :class:`UrgencyUnknown`, naming
    ``field``, when none is stored."""
    held = urgencies.get(urgency_id, transaction=transaction)
    if held is None:
        raise UrgencyUnknown(field, urgency_id)
    return held


class UrgencyReferences:
    """Holds still the urgencies a routine's or a chore's write newly names: :meth:`held` opens one unit locking them
    all and confirms each live under the locks, so no reference lands on an urgency in the moment it is retired
    (``bzh:write-reestablishes-read``)."""

    def __init__(self, *, urgencies: IReadUrgencyRepository, transactions: IHubTransactions) -> None:
        self._urgencies = urgencies
        self._transactions = transactions

    @contextmanager
    def held(self, named: Mapping[str, UrgencyId]) -> Generator[Transaction | None, None, None]:
        """``named`` maps each field that names an urgency to its id. With none named it opens no unit and yields
        ``None``; otherwise it yields the locked unit the write joins. :class:`UrgencyUnknown` or
        :class:`UrgencyRetired`, naming the field, when one is not live under the locks."""
        if not named:
            yield None
            return
        keys = sorted({urgency_lock(urgency_id) for urgency_id in named.values()})
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=keys)
            for field, urgency_id in named.items():
                require_live(field, urgency_id, reread(self._urgencies, field, urgency_id, transaction=locked))
            yield locked


def default_lock_keys(default_id: UrgencyId) -> list[tuple[str, str]]:
    """The keyed locks a mint writing the default ``default_id`` declares: the settings' and the urgency's."""
    return [tenant_settings_lock(), urgency_lock(default_id)]

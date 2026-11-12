"""Urgencies and the tenant's settings in a configuration document (``bzh:config-apply``); the ``urgencies``,
``urgency_order`` and ``settings`` sections are planned here for :mod:`blizzard.hub.domain.config.apply`, in that
order.

An ``urgencies`` entry is keyed by its slug: created when absent — at the least urgent end, in document order — and a
sparse edit of the fields it states when present. An entry naming a retired urgency is refused ``urgency_retired``
rather than enabled, since reviving one is an act an operator takes on purpose; so is any reference to a retired
urgency, and a reference to one neither stored nor declared is refused ``urgency_unknown``. A stated
``urgency_order`` sets the whole live order and names every urgency live once the apply has run, exactly once; a stated
``settings.default_urgency`` sets the default.

Each planned write carries what the plan read — an urgency's revision, the order's sequence, the settings' revision
and the revision of each urgency a write names — and the plan names the keyed locks the writer declares before its
first write, so a retire committing between the plan and the write refuses the apply as stale."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.project_colors import HexColor, ProjectColor
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config import lifecycle
from blizzard.hub.domain.config.apply import (
    SETTINGS_SECTION,
    URGENCIES_SECTION,
    URGENCY_ORDER_SECTION,
    ApplyEntryRefused,
    ApplyPlan,
    EntryOutcome,
    PlannedWrite,
)
from blizzard.hub.domain.config.changes import ChangeContext, ConfigChange, RecordKind
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.kernel.locks import LockKey
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType
from blizzard.hub.domain.projects.color import ColorPick
from blizzard.hub.domain.urgency.mark import IconShape
from blizzard.hub.domain.urgency.model import (
    MarkWrite,
    Urgency,
    UrgencyEdit,
    UrgencyId,
    UrgencyRetired,
    UrgencyShape,
    UrgencySlug,
    UrgencyUnknown,
    require_live,
)
from blizzard.hub.domain.urgency.order import (
    Placed,
    PositionFact,
    UrgencyOrder,
    UrgencyOrderIncomplete,
    order_change,
)
from blizzard.hub.domain.urgency.ports import tenant_settings_lock, urgency_lock, urgency_order_lock
from blizzard.hub.domain.urgency.settings import DEFAULT_URGENCY_FIELD, TenantSettings

#: The outcome-row keys of the two singleton sections.
ORDER_KEY = "urgency_order"
SETTINGS_KEY = "settings"


@domain_model
@dataclass(frozen=True)
class UrgencyDeclaration:
    """An urgency the document names by slug, and each field it states; :data:`UNSET` for one it leaves out."""

    slug: str
    name: str | None | UnsetType = UNSET
    color: str | None | UnsetType = UNSET
    emoji: str | None | UnsetType = UNSET
    icon: IconShape | None | UnsetType = UNSET

    def shape(self) -> UrgencyShape:
        return UrgencyShape(self.slug, self.name, self.color, self.emoji, self.icon)

    def edit(self) -> UrgencyEdit:
        return UrgencyEdit(name=self.name, color=self.color, emoji=self.emoji, icon=self.icon)


@domain_model
@dataclass(frozen=True)
class UrgencySections:
    """The three sections as a document states them: its ``urgencies`` entries, its ``urgency_order`` — ``None`` when
    it leaves the order standing — and its ``settings.default_urgency``, :data:`UNSET` when it leaves the default."""

    urgencies: tuple[UrgencyDeclaration, ...] = ()
    order: tuple[str, ...] | None = None
    default_urgency: str | None | UnsetType = UNSET

    def is_stated(self) -> bool:
        return bool(self.urgencies) or self.order is not None or self.default_urgency is not UNSET


@domain_model
@dataclass(frozen=True)
class StoredUrgencies:
    """What the edge loaded: every urgency of the tenant, retired ones included; the order, read before them; and the
    tenant's settings."""

    tenant_id: str
    urgencies: tuple[Urgency, ...]
    order: UrgencyOrder
    settings: TenantSettings


@domain_model
@dataclass(frozen=True)
class NamedRevision:
    """An urgency a write names, at the revision the plan leaves it at."""

    urgency_id: UrgencyId
    revision: int
    slug: str

    @classmethod
    def of(cls, urgency: Urgency) -> NamedRevision:
        return cls(urgency.urgency_id, urgency.revision, urgency.slug)


@domain_model
@dataclass(frozen=True)
class UrgencyWrite:
    """An urgency's create or edit with the icon bytes stored beside it; a create lands at ``position`` while the
    order's sequence stands at ``order_seq``, the one the plan read."""

    urgency: Urgency
    mark: MarkWrite
    position: float | None = None
    order_seq: int = 0

    @property
    def revision(self) -> int:
        return self.urgency.revision

    def require_revision(self, if_match: int | None) -> None:
        self.urgency.require_revision(if_match)


@domain_model
@dataclass(frozen=True)
class UrgencyOrderWrite:
    """The position facts setting the whole live order, landing only while the order's sequence stands at
    ``from_seq`` — the one the plan read, before any create of the same apply — and each urgency it orders stands at
    the revision ``named`` holds for it."""

    tenant_id: str
    facts: tuple[PositionFact, ...]
    from_seq: int
    named: tuple[NamedRevision, ...]

    @property
    def revision(self) -> int:
        return self.from_seq

    def require_revision(self, if_match: int | None) -> None:
        lifecycle.require_revision(if_match, self.from_seq, kind="urgency_order", key=self.tenant_id)


@domain_model
@dataclass(frozen=True)
class SettingsWrite:
    """The settings naming a new default, landing only while that urgency stands at the revision ``default`` holds."""

    settings: TenantSettings
    default: NamedRevision

    @property
    def revision(self) -> int:
        return self.settings.revision

    def require_revision(self, if_match: int | None) -> None:
        self.settings.require_revision(if_match)


def plan_urgencies(
    declared: UrgencySections,
    stored: StoredUrgencies,
    ctx: ChangeContext,
    *,
    pick: ColorPick,
    at: datetime,
) -> ApplyPlan:
    """The writes, outcome rows and keyed locks that bring the stored urgencies, their order and the settings to
    ``declared``. :class:`ApplyEntryRefused` at the first entry that cannot be applied."""
    planner = _Planner(stored, ctx, at)
    planner.urgencies(declared.urgencies, pick)
    if declared.order is not None:
        planner.order(declared.order)
    if declared.default_urgency is not UNSET:
        planner.default(declared.default_urgency)
    return ApplyPlan(writes=tuple(planner.writes), outcomes=tuple(planner.outcomes), locks=planner.locks())


class _Planner:
    """The urgencies as the plan so far leaves them, and the writes and outcome rows it has planned."""

    def __init__(self, stored: StoredUrgencies, ctx: ChangeContext, at: datetime) -> None:
        self._stored = stored
        self._ctx = ctx
        self._at = at
        self._by_slug: dict[str, Urgency] = {u.slug: u for u in stored.urgencies}
        self._order = stored.order
        self.writes: list[PlannedWrite] = []
        self.outcomes: list[EntryOutcome] = []
        self._positions = False
        self._default: UrgencyId | None = None

    def locks(self) -> tuple[LockKey, ...]:
        """The settings' lock whenever the plan writes — every retire takes it, so none runs beside the apply — the
        set's when it lands a position, and the lock of the urgency it makes the default."""
        if not self.writes:
            return ()
        keys = [tenant_settings_lock()]
        if self._positions:
            keys.append(urgency_order_lock())
        if self._default is not None:
            keys.append(urgency_lock(self._default))
        return tuple(sorted(keys))

    def urgencies(self, declared: Sequence[UrgencyDeclaration], pick: ColorPick) -> None:
        seen: set[str] = set()
        for index, entry in enumerate(declared):
            try:
                if entry.slug in seen:
                    raise ConfigFieldError("slug", f"{entry.slug!r} is named twice in this document")
                seen.add(entry.slug)
                record = self._by_slug.get(UrgencySlug.parse(entry.slug).value)
                if record is None:
                    self._create(index, entry, pick)
                elif record.retired:
                    raise UrgencyRetired("slug", record.slug)
                else:
                    self._edit(index, entry, record)
            except ConfigFieldError as exc:
                raise ApplyEntryRefused(URGENCIES_SECTION, index, exc) from exc

    def _create(self, index: int, entry: UrgencyDeclaration, pick: ColorPick) -> None:
        live = [u.color for u in self._by_slug.values() if not u.retired]
        record, mark, change = Urgency.new(entry.shape(), self._ctx, siblings=lambda: live, pick=pick, at=self._at)
        position, _ = self._order.landing(None, last=True)
        self._order = UrgencyOrder.of(
            (*self._order.placed, Placed(record.urgency_id, record.slug, position)), self._order.seq
        )
        self._by_slug[record.slug] = record
        self._positions = True
        self._write(
            URGENCIES_SECTION, index, UrgencyWrite(record, mark, position, self._stored.order.seq), None, change
        )
        self._outcome(RecordKind.URGENCY, record.slug, change)

    def _edit(self, index: int, entry: UrgencyDeclaration, record: Urgency) -> None:
        decided = record.edit(entry.edit(), self._ctx, if_match=None, at=self._at)
        if decided is None:
            self.outcomes.append(EntryOutcome(RecordKind.URGENCY, record.slug, None))
            return
        edited, mark, change = decided
        self._by_slug[edited.slug] = edited
        self._write(URGENCIES_SECTION, index, UrgencyWrite(edited, mark), record.revision, change)
        self._outcome(RecordKind.URGENCY, edited.slug, change)

    def order(self, references: Sequence[str]) -> None:
        ordered: list[Urgency] = []
        for index, reference in enumerate(references):
            try:
                found = self._live("", reference)
                if found in ordered:
                    raise UrgencyOrderIncomplete(f"urgency {found.slug} is named twice")
                ordered.append(found)
            except ConfigFieldError as exc:
                raise ApplyEntryRefused(URGENCY_ORDER_SECTION, index, exc) from exc
        try:
            facts = self._order.whole([u.urgency_id for u in ordered])
        except ConfigFieldError as exc:
            raise ApplyEntryRefused(URGENCY_ORDER_SECTION, 0, exc) from exc
        if not facts:
            self.outcomes.append(EntryOutcome(RecordKind.URGENCY_ORDER, ORDER_KEY, None))
            return
        after = self._order.after(facts)
        change = order_change(self._ctx, self._stored.tenant_id, self._order, after, at=self._at)
        write = UrgencyOrderWrite(
            self._stored.tenant_id,
            facts,
            self._stored.order.seq,
            tuple(NamedRevision.of(u) for u in ordered),
        )
        self._order = after
        self._positions = True
        self._write(URGENCY_ORDER_SECTION, 0, write, self._stored.order.seq, change)
        self._outcome(RecordKind.URGENCY_ORDER, ORDER_KEY, change)

    def default(self, reference: str | None) -> None:
        try:
            default = self._live(DEFAULT_URGENCY_FIELD, TenantSettings.named_default(reference))
            settings = self._stored.settings
            held = next(u for u in self._by_slug.values() if u.urgency_id == settings.default_urgency_id)
            decided = settings.point_at(default, held=held, ctx=self._ctx, if_match=None, at=self._at)
        except ConfigFieldError as exc:
            raise ApplyEntryRefused(SETTINGS_SECTION, 0, exc) from exc
        if decided is None:
            self.outcomes.append(EntryOutcome(RecordKind.TENANT_SETTINGS, SETTINGS_KEY, None))
            return
        edited, change = decided
        self._default = default.urgency_id
        self._write(SETTINGS_SECTION, 0, SettingsWrite(edited, NamedRevision.of(default)), settings.revision, change)
        self._outcome(RecordKind.TENANT_SETTINGS, SETTINGS_KEY, change)

    def _live(self, field: str, reference: str) -> Urgency:
        """The urgency ``reference`` — an id or a slug — names as the plan leaves the urgencies, when it is live."""
        found = next((u for u in self._by_slug.values() if reference in (u.urgency_id, u.slug)), None)
        if found is None:
            raise UrgencyUnknown(field, reference)
        return require_live(field, reference, found)

    def _write(
        self,
        section: str,
        index: int,
        record: UrgencyWrite | UrgencyOrderWrite | SettingsWrite,
        from_revision: int | None,
        change: ConfigChange,
    ) -> None:
        self.writes.append(PlannedWrite(section, index, record, from_revision, change))

    def _outcome(self, kind: RecordKind, key: str, change: ConfigChange) -> None:
        self.outcomes.append(EntryOutcome(kind, key, change.op, change.diff))


def urgency_color(urgency: Urgency) -> str:
    """The color as a document writes it: a palette name, or a ``#rrggbb``."""
    color: ProjectColor | HexColor = urgency.color
    return color.value if isinstance(color, ProjectColor) else color

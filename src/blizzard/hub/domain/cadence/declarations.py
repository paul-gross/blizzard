"""The cadence entry of a configuration document (``bzh:config-apply``).

An entry is held to the rules a create and an edit are, and to no reference check: a chore's routine and scopes are
named by slug and need not resolve, since what does not is shown as the cadence's attention. An entry is compared
with its stored cadence by effective value — a spacing by its minutes, an unstated anchor as ``started`` — so a
document export writes applies back with no write. A planned write that leaves the cadence admitting work needs
``may_admit``; an entry that already stands as declared plans none, and so asks nothing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.cadence.model import Anchor, Cadence, CadenceEdit, CadenceShape, ScheduleShape, Spacing
from blizzard.hub.domain.config.apply import DeclaredReferences
from blizzard.hub.domain.config.changes import ChangeContext, ConfigChange
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.records import NamedRetirableConfiguredRecord
from blizzard.hub.domain.kernel.unset import UNSET


@domain_model
@dataclass(frozen=True)
class CadenceDeclaration:
    """A cadence the document names by slug within ``project_id``: its create shape, the sparse edit of what it
    states, whether the applying identity may admit work, and the revision of the stored cadence it names
    (``None`` when it names none)."""

    shape: CadenceShape
    edit: CadenceEdit
    project_id: str
    may_admit: bool
    stored_revision: int | None = None

    @property
    def handle(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        assert self.shape.slug is not None
        return self.shape.slug

    @property
    def key(self) -> tuple[str, str]:
        return (self.project_id, self.handle)

    def check(self, refs: DeclaredReferences, stored: NamedRetirableConfiguredRecord | None) -> None:
        """Nothing a chore names is checked: an unresolved one is attention, never a refusal."""
        return None

    def create(self, ctx: ChangeContext, *, at: datetime) -> tuple[Cadence, ConfigChange]:
        return Cadence.new(self.shape, project_id=self.project_id, ctx=ctx, at=at)

    def edit_record(
        self, record: NamedRetirableConfiguredRecord, ctx: ChangeContext, *, at: datetime
    ) -> tuple[Cadence, ConfigChange] | None:
        assert isinstance(record, Cadence)
        return record.edit(effective_edit(self.edit, record), ctx, if_match=None, at=at)

    def settle(
        self, record: NamedRetirableConfiguredRecord, refs: DeclaredReferences, ctx: ChangeContext, *, at: datetime
    ) -> None:
        """Refuse a planned write that leaves the cadence admitting work the applier may not admit."""
        assert isinstance(record, Cadence)
        if record.revision != self.stored_revision:
            record.require_admission(may_admit=self.may_admit)


def effective_edit(edit: CadenceEdit, stored: Cadence) -> CadenceEdit:
    """``edit`` without each stated field whose effective value ``stored`` already holds; a field that does not
    parse is kept, so the edit refuses it naming the field."""
    rhythm = stored.rhythm
    if edit.name is not UNSET and edit.name == stored.name:
        edit = replace(edit, name=UNSET)
    held_schedule = None if rhythm.schedule is None else ScheduleShape(rhythm.schedule.cron, rhythm.schedule.zone)
    if edit.schedule is not UNSET and edit.schedule == held_schedule:
        edit = replace(edit, schedule=UNSET)
    held_minutes = None if rhythm.spacing is None else rhythm.spacing.minutes
    if edit.spacing is not UNSET and _minutes(edit.spacing) == held_minutes:
        edit = replace(edit, spacing=UNSET)
    # An anchor counts a spacing; with one held, an unstated anchor reads as ``started`` on both sides.
    if (
        edit.anchor is not UNSET
        and rhythm.spacing is not None
        and (_anchor(edit.anchor) or Anchor.STARTED) == rhythm.effective_anchor()
    ):
        edit = replace(edit, anchor=UNSET)
    return edit


def _minutes(spacing: str | None) -> int | None | str:
    """The minutes ``spacing`` states, ``None`` for none, or the raw text when it does not parse."""
    if spacing is None:
        return None
    try:
        return Spacing.parse(spacing).minutes
    except ConfigFieldError:
        return spacing


def _anchor(anchor: str | None) -> Anchor | None | str:
    """The anchor ``anchor`` states, ``None`` for none, or the raw text when it names none of the three."""
    if anchor is None:
        return None
    try:
        return Anchor(anchor)
    except ValueError:
        return anchor

"""The hub-owned work item's own rules — its state, the verbs legal from each state, the
non-blank text invariant, and how an edit resolves against the item it targets.

Pure (``bzh:domain-core``): every rule takes the loaded :class:`HubWorkItem` and plain values
and returns a decision or raises a refusal, so the services that hold the work-item write
port only orchestrate around it (``bzh:domain-orchestration-split``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from blizzard.foundation.roles import domain_model
from blizzard.foundation.work_items import WorkItemClosure, WorkItemPriority
from blizzard.hub.domain.chunk.model import HubWorkItem
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType


class WorkItemState(StrEnum):
    """A work item's lifecycle state — ``open`` until it carries a closure, then the
    closure itself. Closure is terminal: nothing reopens an item."""

    OPEN = "open"
    DELIVERED = "delivered"
    WITHDRAWN = "withdrawn"

    @classmethod
    def of(cls, item: HubWorkItem) -> WorkItemState:
        if item.closure is None:
            return cls.OPEN
        return cls(item.closure.value)

    def on(self, verb: WorkItemVerb) -> Transition:
        """What ``verb`` does to an item in this state — the one declared table every
        work-item verb reads."""
        return _TRANSITIONS[verb][self]


class WorkItemVerb(StrEnum):
    """Every verb that reads or changes a work item's state: the operator's ``EDIT`` and ``WITHDRAW``,
    an update proposal's ``APPEND_EVIDENCE``, the close-intent drain's ``DELIVER``, and a holding
    chunk's read-through ``READ``."""

    EDIT = "edit"
    WITHDRAW = "withdraw"
    APPEND_EVIDENCE = "append-evidence"
    DELIVER = "deliver"
    READ = "read"


class Transition(StrEnum):
    """A verb's effect from one state: it applies, it is an idempotent no-op that
    writes nothing and leaves the closure standing, or it is refused."""

    APPLY = "apply"
    NOOP = "noop"
    REFUSE = "refuse"


_OPEN_ONLY: Mapping[WorkItemState, Transition] = {
    WorkItemState.OPEN: Transition.APPLY,
    WorkItemState.DELIVERED: Transition.REFUSE,
    WorkItemState.WITHDRAWN: Transition.REFUSE,
}

_OPEN_ELSE_NOOP: Mapping[WorkItemState, Transition] = {
    WorkItemState.OPEN: Transition.APPLY,
    WorkItemState.DELIVERED: Transition.NOOP,
    WorkItemState.WITHDRAWN: Transition.NOOP,
}

# Deliver from withdrawn is a no-op, not a refusal: the holder's code landed, so the drain still reports.
_TRANSITIONS: Mapping[WorkItemVerb, Mapping[WorkItemState, Transition]] = {
    WorkItemVerb.EDIT: _OPEN_ONLY,
    WorkItemVerb.WITHDRAW: _OPEN_ONLY,
    WorkItemVerb.APPEND_EVIDENCE: _OPEN_ONLY,
    WorkItemVerb.DELIVER: _OPEN_ELSE_NOOP,
    WorkItemVerb.READ: {
        WorkItemState.OPEN: Transition.APPLY,
        WorkItemState.DELIVERED: Transition.APPLY,
        WorkItemState.WITHDRAWN: Transition.REFUSE,
    },
}


class WorkItemNotEditable(Exception):
    """An edit or withdrawal targeted a work item that already carries a closure —
    closure is terminal, so neither verb is retroactive."""

    def __init__(self, work_item_id: str, closure: WorkItemClosure) -> None:
        super().__init__(f"work item {work_item_id} is {closure.value}, not editable")
        self.work_item_id = work_item_id
        self.closure = closure


def require_open_for(item: HubWorkItem, verb: WorkItemVerb) -> None:
    """Refuse an operator ``verb`` the table refuses from ``item``'s state, naming the
    closure that stands."""
    if WorkItemState.of(item).on(verb) is Transition.REFUSE:
        assert item.closure is not None
        raise WorkItemNotEditable(item.work_item_id, item.closure)


def require_withdrawn(closed: HubWorkItem) -> None:
    """Refuse a withdraw whose write landed nothing: a delivery closed ``closed`` between the
    guard and the write, so the closure that stands is not ``withdrawn``."""
    if closed.closure is not WorkItemClosure.WITHDRAWN:
        assert closed.closure is not None
        raise WorkItemNotEditable(closed.work_item_id, closed.closure)


def is_readable(item: HubWorkItem) -> bool:
    """Whether a holding chunk may still read ``item`` — a delivered item stays readable,
    a withdrawn one does not."""
    return WorkItemState.of(item).on(WorkItemVerb.READ) is Transition.APPLY


class WorkItemFieldBlank(Exception):
    """A work item's title or body was empty once stripped — every item carries both."""

    def __init__(self, field_name: str) -> None:
        super().__init__(f"{field_name} must not be blank")
        self.field_name = field_name


def require_text(value: str, field_name: str) -> str:
    """``value`` stripped, or :class:`WorkItemFieldBlank` when nothing is left."""
    text = value.strip()
    if not text:
        raise WorkItemFieldBlank(field_name)
    return text


@domain_model
@dataclass(frozen=True)
class WorkItemText:
    """A work item's title and body, each stripped and non-blank — the invariant every
    door that mints an item holds, title checked first."""

    title: str
    body: str

    @classmethod
    def of(cls, *, title: str, body: str) -> WorkItemText:
        return cls(title=require_text(title, "title"), body=require_text(body, "body"))


@domain_model
@dataclass(frozen=True)
class WorkItemRevision:
    """The full field set an edit writes — every field resolved, none left unset."""

    title: str
    body: str
    stated_priority: str | None


@domain_model
@dataclass(frozen=True)
class WorkItemEdit:
    """The fields a single all-or-nothing item edit request supplies, the same sentinel
    shape :class:`~blizzard.hub.domain.operations.edit.ChunkEdit` carries: a field absent
    from the edit is left unchanged, distinct from an explicit clear. A supplied title or
    body is stripped and refused when blank, title first."""

    title: str | UnsetType = field(default=UNSET)
    body: str | UnsetType = field(default=UNSET)
    stated_priority: WorkItemPriority | None | UnsetType = field(default=UNSET)

    def __post_init__(self) -> None:
        if self.title is not UNSET:
            object.__setattr__(self, "title", require_text(self.title, "title"))
        if self.body is not UNSET:
            object.__setattr__(self, "body", require_text(self.body, "body"))

    def resolve_against(self, item: HubWorkItem) -> WorkItemRevision:
        """The revision this edit writes over ``item``: an unset field keeps ``item``'s
        value, an explicit ``None`` priority clears it. An empty edit resolves to ``item``'s
        own fields, and still revises it."""
        if self.stated_priority is UNSET:
            stated_priority = item.stated_priority
        else:
            stated_priority = self.stated_priority.value if self.stated_priority is not None else None
        return WorkItemRevision(
            title=item.title if self.title is UNSET else self.title,
            body=item.body if self.body is UNSET else self.body,
            stated_priority=stated_priority,
        )

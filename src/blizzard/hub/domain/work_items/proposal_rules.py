"""How one fleet work-item proposal is judged — pure (``bzh:domain-core``): each rule takes
the stamped proposal row, or the item its pointer loaded, and returns either the work to do
or an :class:`Unresolvable` judgment carrying its reason, which the materialization sweep
records once and never re-judges."""

from __future__ import annotations

import json
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import HubWorkItem, WorkItemAuthor, WorkRef
from blizzard.hub.domain.chunk.proposals import StampedWorkItemProposal
from blizzard.hub.domain.work_items.model import (
    Transition,
    WorkItemFieldBlank,
    WorkItemState,
    WorkItemText,
    WorkItemVerb,
)

# A row whose ``data`` is not the JSON object its kind needs fails one of these.
_MALFORMED = (json.JSONDecodeError, KeyError, TypeError, AttributeError)


@domain_model
@dataclass(frozen=True)
class Unresolvable:
    """A proposal judged unresolvable — terminal, recorded with ``reason`` and, for an
    update, the pointer it named. ``malformed`` marks a row whose own data failed to parse."""

    reason: str
    pointer: WorkRef | None = None
    malformed: bool = False


@domain_model
@dataclass(frozen=True)
class CreateProposal:
    """A judged ``create`` proposal, ready to mint into the hub source."""

    text: WorkItemText
    stated_priority: str | None
    author: WorkItemAuthor


@domain_model
@dataclass(frozen=True)
class UpdateProposal:
    """A parsed ``update`` proposal: the evidence to append to the item ``pointer`` names."""

    pointer: WorkRef
    evidence: str


def _malformed(exc: Exception) -> Unresolvable:
    return Unresolvable(reason=f"malformed proposal data: {exc}", malformed=True)


def parse_proposal(row: StampedWorkItemProposal) -> CreateProposal | UpdateProposal | Unresolvable:
    """Classify ``row``: a ``create`` kind is judged for minting, anything else parses as an
    update. A create with no proposing runner, or with a blank title or body, is
    unresolvable; data that is not the object its kind needs is unresolvable as malformed."""
    try:
        data = json.loads(row.data)
        if row.kind == "create":
            return _judge_create(row, data)
        return UpdateProposal(pointer=WorkRef(source=data["source"], ref=data["ref"]), evidence=data["evidence"])
    except _MALFORMED as exc:
        return _malformed(exc)


def _judge_create(row: StampedWorkItemProposal, data: dict) -> CreateProposal | Unresolvable:
    if row.runner_id is None:
        return Unresolvable(reason="no proposing runner recorded for this proposal")
    title, body, stated_priority = data["title"], data["body"], data.get("stated_priority")
    try:
        text = WorkItemText.of(title=title, body=body)
    except WorkItemFieldBlank as exc:
        return Unresolvable(reason=str(exc))
    return CreateProposal(
        text=text,
        stated_priority=stated_priority,
        author=WorkItemAuthor.fleet(runner_id=row.runner_id, chunk_id=row.chunk_id, node_name=row.node_name),
    )


def judge_update_target(item: HubWorkItem | None, *, pointer: WorkRef, editable: bool) -> Unresolvable | None:
    """Whether the item an update proposal names can take its evidence: its source must
    carry the editor capability, the item must exist, and appending evidence must be legal
    from its state. ``None`` means append."""
    if not editable:
        return Unresolvable(reason=f"source {pointer.source!r} has no editor", pointer=pointer)
    if item is None:
        return Unresolvable(reason="item does not exist", pointer=pointer)
    state = WorkItemState.of(item)
    if state.on(WorkItemVerb.APPEND_EVIDENCE) is Transition.REFUSE:
        return Unresolvable(reason=f"item is {state.value}", pointer=pointer)
    return None

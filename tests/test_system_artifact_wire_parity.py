"""Docs-as-contract guard (D5, blizzard#391 Phase 2) — every published garden system
artifact carries a machine-checkable pin to the wire models it documents: a `### ModelName`
heading immediately followed by a fenced ```json block whose keys are exactly that model's
field aliases, and which parses into the model without error. Renaming, adding, or removing a
field on any pinned model breaks this test until the document is updated to match."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

import pytest
from pydantic import BaseModel

from blizzard.hub.system_artifacts import PACKAGED
from blizzard.wire import finding, garden_proposal


def _finding_op_members() -> dict[str, type[BaseModel]]:
    """``finding.FindingOp``'s three current member types, introspected off the union alias
    itself (``Annotated[AddFindingOp | ObservedFindingOp | GoneFindingOp,
    Field(discriminator="op")]``) rather than named by hand — so a member added to the union
    later is pinned here automatically, and ``test_every_pinned_model_is_covered_by_some_document``
    catches it going undocumented rather than silently passing."""
    (union,) = get_args(finding.FindingOp)[:1]
    return {member.__name__: member for member in get_args(union)}


pytestmark = pytest.mark.unit

# The documented models, by heading name. `finding.FindingOp` is a union alias, not a model,
# so it's absent here — a document pins its three member types individually instead.
_MODELS: dict[str, type[BaseModel]] = {
    "FindingCandidate": finding.FindingCandidate,
    "FindingDelta": finding.FindingDelta,
    **_finding_op_members(),
    "GardenProposalCandidate": garden_proposal.GardenProposalCandidate,
}

# `### ModelName`, then a fenced ```json block. Non-greedy body so consecutive pairs in
# one document are extracted independently rather than one match swallowing the rest.
_HEADING_AND_BLOCK = re.compile(r"^### (?P<name>\w+)\n+```json\n(?P<body>.*?)\n```", re.MULTILINE | re.DOTALL)


def _all_documents() -> list[Path]:
    docs = PACKAGED.paths
    assert docs, f"no packaged system-artifact documents found under {PACKAGED.root}"
    return docs


def _guard_pairs(path: Path) -> list[tuple[str, str]]:
    return [(m.group("name"), m.group("body")) for m in _HEADING_AND_BLOCK.finditer(path.read_text())]


def _model_field_aliases(model: type[BaseModel]) -> set[str]:
    return {field.alias or name for name, field in model.model_fields.items()}


@pytest.mark.parametrize("doc", _all_documents(), ids=lambda p: p.name)
def test_document_carries_at_least_one_guard_pair(doc: Path) -> None:
    """A document with no `### Model` / ```json pair at all would silently let D5 lapse
    for its whole file rather than failing loudly."""
    assert _guard_pairs(doc), f"{doc} carries no '### Model' + ```json guard pair"


@pytest.mark.parametrize("doc", _all_documents(), ids=lambda p: p.name)
def test_every_guard_pair_matches_its_model_exactly(doc: Path) -> None:
    for name, body in _guard_pairs(doc):
        model = _MODELS.get(name)
        assert model is not None, f"{doc}: '### {name}' names no known wire model (add it to _MODELS if new)"
        payload = json.loads(body)
        assert isinstance(payload, dict), f"{doc}: '### {name}' json block is not a JSON object"
        expected = _model_field_aliases(model)
        assert set(payload.keys()) == expected, (
            f"{doc}: '### {name}' json block's keys {set(payload.keys())} != {model.__name__}'s fields {expected}"
        )
        # Must also actually parse into the model, not merely carry the right key set.
        model.model_validate(payload)


def test_every_pinned_model_is_covered_by_some_document() -> None:
    """The inverse of the per-pair check — a model dropped from every document (a rename
    nobody re-headed) fails here rather than passing by simple omission."""
    covered = {name for doc in _all_documents() for name, _ in _guard_pairs(doc)}
    assert covered == set(_MODELS), f"documented models {covered} != the pinned set {set(_MODELS)}"


def test_finding_op_members_are_introspected_off_the_union_not_named_by_hand() -> None:
    """Pins the introspection mechanism itself: a member added to, removed from, or renamed
    in the ``FindingOp`` union changes what this derives with no edit to this test file —
    the opposite of a hardcoded name list, which a new member could silently bypass."""
    assert _finding_op_members() == {
        "AddFindingOp": finding.AddFindingOp,
        "ObservedFindingOp": finding.ObservedFindingOp,
        "GoneFindingOp": finding.GoneFindingOp,
    }

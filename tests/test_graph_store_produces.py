"""``graph_nodes.produces`` JSON encode/decode (unit tier, issue #143).

The column stays JSON ``TEXT`` across D1 (no migration): a legacy row minted before
#143 carries a plain ``list[str]``, and a row minted since carries
``list[{name, kind}]``. :func:`~blizzard.hub.store.internal.graph_store._produces_specs`
is the one read-time seam that normalizes both to :class:`ProducesSpec` —
this pins both directions, and the round trip through
:func:`~blizzard.hub.store.internal.graph_store._produces_spec_to_json`.
"""

from __future__ import annotations

import json

import pytest

from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.graph import ProducesSpec
from blizzard.hub.store.internal.graph_store import _produces_spec_to_json, _produces_specs

pytestmark = pytest.mark.unit


def test_produces_specs_reads_none_as_empty() -> None:
    assert _produces_specs(None) == []


def test_produces_specs_normalizes_a_legacy_string_list_row_to_asset_specs() -> None:
    legacy = json.dumps(["review-findings", "review-diary"])
    assert _produces_specs(legacy) == [
        ProducesSpec(name="review-findings", kind=ArtifactKind.ASSET),
        ProducesSpec(name="review-diary", kind=ArtifactKind.ASSET),
    ]


def test_produces_specs_reads_the_current_name_kind_mapping_row() -> None:
    current = json.dumps([{"name": "commit", "kind": "git_commit"}, {"name": "notes", "kind": "asset"}])
    assert _produces_specs(current) == [
        ProducesSpec(name="commit", kind=ArtifactKind.GIT_COMMIT),
        ProducesSpec(name="notes", kind=ArtifactKind.ASSET),
    ]


def test_produces_spec_to_json_round_trips_through_produces_specs() -> None:
    specs = [ProducesSpec(name="commit", kind=ArtifactKind.GIT_COMMIT), ProducesSpec(name="notes")]
    dumped = json.dumps([_produces_spec_to_json(s) for s in specs])
    assert _produces_specs(dumped) == specs

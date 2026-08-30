"""``graph_nodes.produces`` JSON encode/decode (unit tier, issue #143).

The column stays JSON ``TEXT`` across D1 (no migration): a legacy row carries a plain
``list[str]``, a row minted since carries ``list[{name, kind}]``, and
:class:`~blizzard.hub.store.internal.graph_store.ProducesColumn` normalizes both to
:class:`ProducesSpec`; this pins both directions plus the round trip."""

from __future__ import annotations

import json

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.hub.domain.graph import ProducesSpec
from blizzard.hub.store.internal.graph_store import PRODUCES

pytestmark = pytest.mark.unit


def test_produces_specs_reads_none_as_empty() -> None:
    assert PRODUCES.decode(None) == []


def test_produces_specs_normalizes_a_legacy_string_list_row_to_asset_specs() -> None:
    legacy = json.dumps(["review-findings", "review-diary"])
    assert PRODUCES.decode(legacy) == [
        ProducesSpec(name="review-findings", kind=ArtifactKind.ASSET),
        ProducesSpec(name="review-diary", kind=ArtifactKind.ASSET),
    ]


def test_produces_specs_reads_the_current_name_kind_mapping_row() -> None:
    current = json.dumps([{"name": "commit", "kind": "git_commit"}, {"name": "notes", "kind": "asset"}])
    assert PRODUCES.decode(current) == [
        ProducesSpec(name="commit", kind=ArtifactKind.GIT_COMMIT),
        ProducesSpec(name="notes", kind=ArtifactKind.ASSET),
    ]


def test_produces_encode_round_trips_through_decode() -> None:
    specs = [ProducesSpec(name="commit", kind=ArtifactKind.GIT_COMMIT), ProducesSpec(name="notes")]
    assert PRODUCES.decode(PRODUCES.encode(specs)) == specs

"""``BLIZZARD_TEST_SHARD`` splits the collected suite into disjoint shards that together cover it."""

from __future__ import annotations

import pytest

from tests.conftest import parse_shard, shard_of

pytestmark = pytest.mark.unit

_NODEIDS = [f"tests/test_mod_{m}.py::test_case[{p}]" for m in range(40) for p in range(25)]


def test_every_test_lands_in_exactly_one_shard() -> None:
    shards = [shard_of(nodeid, 4) for nodeid in _NODEIDS]
    assert set(shards) == {1, 2, 3, 4}


def test_shards_split_a_large_suite_roughly_evenly() -> None:
    sizes = [sum(1 for nodeid in _NODEIDS if shard_of(nodeid, 4) == i) for i in (1, 2, 3, 4)]
    assert min(sizes) > len(_NODEIDS) / 4 * 0.8


def test_one_shard_keeps_everything() -> None:
    assert {shard_of(nodeid, 1) for nodeid in _NODEIDS} == {1}


@pytest.mark.parametrize(("spec", "expected"), [("1/4", (1, 4)), ("4/4", (4, 4)), ("1/1", (1, 1))])
def test_parse_shard_reads_index_and_count(spec: str, expected: tuple[int, int]) -> None:
    assert parse_shard(spec) == expected


@pytest.mark.parametrize("spec", ["0/4", "5/4", "4", "a/4", "1/", "/4", "-1/4", "1/0"])
def test_parse_shard_rejects_a_malformed_spec(spec: str) -> None:
    with pytest.raises(pytest.UsageError):
        parse_shard(spec)

"""``ConfigObjectCache`` key composition over a fake revisions read (unit tier)."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.config.changes import RecordKind
from blizzard.hub.domain.config.revisions import ConfigRevisions
from blizzard.hub.live_config import CacheKey, ConfigObjectCache

pytestmark = pytest.mark.unit


class _Revisions:
    def __init__(self) -> None:
        self.rows: dict[tuple[RecordKind, str], ConfigRevisions] = {}

    def revisions(self, kind: RecordKind, key: str) -> ConfigRevisions | None:
        return self.rows.get((kind, key))


class _Obj:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_the_key_carries_kind_key_and_every_revision() -> None:
    key = CacheKey.of(RecordKind.REPOSITORY, "r", ConfigRevisions(revision=3, secret_name="gh", secret_revision=7))
    assert key == CacheKey(RecordKind.REPOSITORY, "r", 3, "gh", 7)


@pytest.mark.parametrize(
    "moved",
    [
        ConfigRevisions(revision=2, secret_name="gh", secret_revision=1),
        ConfigRevisions(revision=1, secret_name="other", secret_revision=1),
        ConfigRevisions(revision=1, secret_name="gh", secret_revision=2),
    ],
)
def test_any_moved_component_rebuilds(moved: ConfigRevisions) -> None:
    revisions = _Revisions()
    revisions.rows[(RecordKind.WORK_SOURCE, "w")] = ConfigRevisions(revision=1, secret_name="gh", secret_revision=1)
    cache = ConfigObjectCache(revisions)
    first = cache.get(RecordKind.WORK_SOURCE, "w", _Obj)
    revisions.rows[(RecordKind.WORK_SOURCE, "w")] = moved

    second = cache.get(RecordKind.WORK_SOURCE, "w", _Obj)

    assert first is not None and first.closed and second is not first


def test_kinds_sharing_a_key_are_separate_entries() -> None:
    revisions = _Revisions()
    same = ConfigRevisions(revision=1, secret_name="gh", secret_revision=1)
    revisions.rows[(RecordKind.WORK_SOURCE, "x")] = same
    revisions.rows[(RecordKind.REPOSITORY, "x")] = same
    cache = ConfigObjectCache(revisions)

    assert cache.get(RecordKind.WORK_SOURCE, "x", _Obj) is not cache.get(RecordKind.REPOSITORY, "x", _Obj)


def test_a_vanished_record_closes_its_entry() -> None:
    revisions = _Revisions()
    revisions.rows[(RecordKind.SECRET, "s")] = ConfigRevisions(revision=1, secret_name="s", secret_revision=1)
    cache = ConfigObjectCache(revisions)
    first = cache.get(RecordKind.SECRET, "s", _Obj)
    del revisions.rows[(RecordKind.SECRET, "s")]

    assert cache.get(RecordKind.SECRET, "s", _Obj) is None
    assert first is not None and first.closed

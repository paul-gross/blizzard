"""The numbered PR / reopen marker vocabulary: exact names and content, lookup, reopen matching (unit tier)."""

from __future__ import annotations

import json

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.hub.domain.actions.pr_markers import (
    is_reopened,
    latest_pr_marker,
    pr_marker_content,
    pr_marker_name,
    pr_marker_number,
    reopen_marker_content,
    reopen_marker_name,
)
from blizzard.hub.domain.artifact.model import StoredArtifact


def _row(name: str, *, epoch: int = 1) -> StoredArtifact:
    return StoredArtifact(
        kind=ArtifactKind.ASSET,
        name=name,
        data="{}",
        repo=None,
        forge=None,
        artifact_id=f"art_{name}_{epoch}",
        chunk_id="ch1",
        node_id="n1",
        node_name="deliver",
        epoch=epoch,
    )


def test_names_and_content_are_byte_exact() -> None:
    assert pr_marker_name("acme/widget", 7) == "delivery-pr/acme/widget/7"
    assert pr_marker_content("acme/widget", 7, "https://x/7") == (
        '{"repo": "acme/widget", "number": 7, "url": "https://x/7"}'
    )
    assert json.loads(pr_marker_content("a/b", 1, "u")) == {"repo": "a/b", "number": 1, "url": "u"}
    assert reopen_marker_name("acme/widget", _row("delivery-pr/acme/widget/7")) == "park-reopen/acme/widget/7"
    assert reopen_marker_content() == ""


def test_write_lookup_number_round_trip() -> None:
    marker = _row(pr_marker_name("acme/widget", 12))
    found = latest_pr_marker([marker], "acme/widget")
    assert found is marker
    assert found is not None
    assert pr_marker_number(found) == 12


def test_lookup_is_isolated_by_repository_and_ignores_the_bare_form() -> None:
    rows = [_row("delivery-pr/acme/widget-x/3"), _row("delivery-pr/acme/widget")]
    assert latest_pr_marker(rows, "acme/widget") is None


def test_lookup_takes_the_newest_in_sequence_order_across_epochs() -> None:
    old, new = _row("delivery-pr/acme/widget/1", epoch=1), _row("delivery-pr/acme/widget/2", epoch=2)
    assert latest_pr_marker([old, new], "acme/widget") is new
    assert latest_pr_marker([new, old], "acme/widget") is old


def test_reopen_matches_only_in_the_given_epoch() -> None:
    marker = _row("delivery-pr/acme/widget/4")
    assert is_reopened([marker, _row("park-reopen/acme/widget/4", epoch=2)], "acme/widget", marker, 2)
    assert not is_reopened([marker, _row("park-reopen/acme/widget/4", epoch=1)], "acme/widget", marker, 2)
    assert not is_reopened([marker, _row("park-reopen/acme/widget/5", epoch=2)], "acme/widget", marker, 2)


def test_a_malformed_suffix_is_matched_as_written_and_only_the_number_parse_raises() -> None:
    marker = _row("delivery-pr/acme/widget/x")
    assert is_reopened([_row("park-reopen/acme/widget/x")], "acme/widget", marker, 1)
    with pytest.raises(ValueError):
        pr_marker_number(marker)

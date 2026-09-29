"""The shared, persistence-free delivery projection."""

from __future__ import annotations

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.delivery_read import DeliveryRead, DeliverySources
from blizzard.hub.domain.work import ChunkFacts

pytestmark = pytest.mark.unit


def marker(name: str, data: str, *, epoch: int = 1) -> ArtifactRow:
    return ArtifactRow(ArtifactKind.ASSET, name, data, None, None, f"art_{epoch}_{name}", "ch", "nd", "deliver", epoch)


def test_partial_land_auto_prs_are_not_human_waits_and_use_each_repos_forge() -> None:
    view = DeliveryRead.of(
        ChunkFacts(minted=True),
        DeliverySources(
            markers=[
                marker(
                    "delivery-pr/acme/one", '{"repo":"acme/one","number":1,"url":"https://forge.one/acme/one/pull/1"}'
                ),
                marker(
                    "delivery-pr/acme/two", '{"repo":"acme/two","number":2,"url":"https://forge.two/acme/two/pull/2"}'
                ),
                marker("merged/acme/one", "merge-sha"),
            ]
        ),
    )
    assert [(p.repo, p.number) for p in view.open_prs] == [("acme/two", 2)]
    assert [p.repo for p in view.closed_prs] == ["acme/one"]
    assert view.awaiting_external_merge is False
    assert [(r.repo, r.commit_hash, r.url) for r in view.landed_repos] == [
        ("acme/one", "merge-sha", "https://forge.one/acme/one/commit/merge-sha")
    ]


def test_landed_repo_whose_pr_is_on_another_forge_origin_has_no_commit_link() -> None:
    sources = DeliverySources(
        markers=[
            marker("delivery-pr/acme/one", '{"repo":"acme/one","number":1,"url":"https://wrong/acme/other/pull/1"}'),
        ],
        legacy_landed={"acme/one": "merged-sha"},
    )
    view = DeliveryRead.of(ChunkFacts(minted=True), sources)
    assert view.open_prs == []
    assert [p.repo for p in view.closed_prs] == ["acme/one"]
    assert view.awaiting_external_merge is False
    assert view.landed_repos[0].url is None


def test_authored_external_merge_marker_requires_a_still_open_pr() -> None:
    sources = DeliverySources(
        markers=[
            marker("delivery-pr/acme/one", '{"repo":"acme/one","number":3,"url":"http://forge/acme/one/pull/3"}'),
            marker("awaiting-external-merge", "human review"),
        ]
    )
    assert DeliveryRead.of(ChunkFacts(minted=True), sources).awaiting_external_merge
    landed = DeliverySources(sources.markers, legacy_landed={"acme/one": "merge-sha"})
    assert not DeliveryRead.of(ChunkFacts(minted=True), landed).awaiting_external_merge


def test_replacement_pr_in_same_epoch_closes_first_reference_without_a_closure_fact() -> None:
    sources = DeliverySources(
        markers=[
            marker("delivery-pr/acme/one", '{"repo":"acme/one","number":3,"url":"http://forge/acme/one/pull/3"}'),
            marker("delivery-pr/acme/one/4", '{"repo":"acme/one","number":4,"url":"http://forge/acme/one/pull/4"}'),
        ]
    )
    view = DeliveryRead.of(ChunkFacts(minted=True), sources)
    assert [p.number for p in view.open_prs] == [4]
    assert [p.number for p in view.closed_prs] == [3]
    assert view.awaiting_external_merge is False


def test_old_external_merge_signal_does_not_label_a_later_auto_pr_as_human_wait() -> None:
    sources = DeliverySources(
        markers=[
            marker("delivery-pr/acme/one/3", '{"repo":"acme/one","number":3,"url":"http://forge/acme/one/pull/3"}'),
            marker("awaiting-external-merge", "human review"),
            marker(
                "delivery-pr/acme/one/4", '{"repo":"acme/one","number":4,"url":"http://forge/acme/one/pull/4"}', epoch=2
            ),
        ]
    )
    view = DeliveryRead.of(ChunkFacts(minted=True), sources)
    assert [p.number for p in view.open_prs] == [4]
    assert [p.number for p in view.closed_prs] == [3]
    assert view.awaiting_external_merge is False

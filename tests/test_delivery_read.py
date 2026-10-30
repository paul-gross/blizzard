"""The shared, persistence-free delivery projection."""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.delivery_read import DeliveryRead, DeliverySources, PrMarker
from blizzard.hub.domain.chunk.model import ChunkFacts, LandedRepos
from blizzard.hub.domain.chunk.no_code import no_code_marker

pytestmark = pytest.mark.unit


def marker(name: str, data: str, *, epoch: int = 1) -> StoredArtifact:
    return StoredArtifact(
        ArtifactKind.ASSET, name, data, None, None, f"art_{epoch}_{name}", "ch", "nd", "deliver", epoch
    )


def pr(name: str, data: str, *, epoch: int = 1) -> PrMarker:
    payload = json.loads(data)
    return PrMarker(name, epoch, payload["repo"], payload["number"], payload["url"])


def delivery_sources(
    markers: Sequence[StoredArtifact | PrMarker] = (), *, legacy_landed: dict[str, str] | None = None
) -> DeliverySources:
    """The sources the store would hand over: the PR markers parsed apart from the other markers."""
    return DeliverySources(
        markers=[m for m in markers if isinstance(m, StoredArtifact)],
        pr_markers=[m for m in markers if isinstance(m, PrMarker)],
        legacy_landed=legacy_landed or {},
    )


def test_partial_land_auto_prs_are_not_human_waits_and_use_each_repos_forge() -> None:
    view = DeliveryRead.of(
        ChunkFacts(minted=True),
        delivery_sources(
            markers=[
                pr("delivery-pr/acme/one", '{"repo":"acme/one","number":1,"url":"https://forge.one/acme/one/pull/1"}'),
                pr("delivery-pr/acme/two", '{"repo":"acme/two","number":2,"url":"https://forge.two/acme/two/pull/2"}'),
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
    sources = delivery_sources(
        markers=[
            pr("delivery-pr/acme/one", '{"repo":"acme/one","number":1,"url":"https://wrong/acme/other/pull/1"}'),
        ],
        legacy_landed={"acme/one": "merged-sha"},
    )
    view = DeliveryRead.of(ChunkFacts(minted=True), sources)
    assert view.open_prs == []
    assert [p.repo for p in view.closed_prs] == ["acme/one"]
    assert view.awaiting_external_merge is False
    assert view.landed_repos[0].url is None


def test_authored_external_merge_marker_requires_a_still_open_pr() -> None:
    sources = delivery_sources(
        markers=[
            pr("delivery-pr/acme/one", '{"repo":"acme/one","number":3,"url":"http://forge/acme/one/pull/3"}'),
            marker("awaiting-external-merge", "human review"),
        ]
    )
    assert DeliveryRead.of(ChunkFacts(minted=True), sources).awaiting_external_merge
    landed = delivery_sources(sources.markers + sources.pr_markers, legacy_landed={"acme/one": "merge-sha"})
    assert not DeliveryRead.of(ChunkFacts(minted=True), landed).awaiting_external_merge


def test_replacement_pr_in_same_epoch_closes_first_reference_without_a_closure_fact() -> None:
    sources = delivery_sources(
        markers=[
            pr("delivery-pr/acme/one", '{"repo":"acme/one","number":3,"url":"http://forge/acme/one/pull/3"}'),
            pr("delivery-pr/acme/one/4", '{"repo":"acme/one","number":4,"url":"http://forge/acme/one/pull/4"}'),
        ]
    )
    view = DeliveryRead.of(ChunkFacts(minted=True), sources)
    assert [p.number for p in view.open_prs] == [4]
    assert [p.number for p in view.closed_prs] == [3]
    assert view.awaiting_external_merge is False


def test_old_external_merge_signal_does_not_label_a_later_auto_pr_as_human_wait() -> None:
    sources = delivery_sources(
        markers=[
            pr("delivery-pr/acme/one/3", '{"repo":"acme/one","number":3,"url":"http://forge/acme/one/pull/3"}'),
            marker("awaiting-external-merge", "human review"),
            pr(
                "delivery-pr/acme/one/4", '{"repo":"acme/one","number":4,"url":"http://forge/acme/one/pull/4"}', epoch=2
            ),
        ]
    )
    view = DeliveryRead.of(ChunkFacts(minted=True), sources)
    assert [p.number for p in view.open_prs] == [4]
    assert [p.number for p in view.closed_prs] == [3]
    assert view.awaiting_external_merge is False


def test_same_epoch_replacement_follows_write_order_not_artifact_id() -> None:
    """The later-written reference carries the lexically smaller id, as two ids minted in
    one millisecond can; the markers' given order is the write order and it decides."""
    earlier = pr("delivery-pr/acme/one/3", '{"repo":"acme/one","number":3,"url":"http://forge/acme/one/pull/3"}')
    later = pr("delivery-pr/acme/one/4", '{"repo":"acme/one","number":4,"url":"http://forge/acme/one/pull/4"}')
    view = DeliveryRead.of(ChunkFacts(minted=True), delivery_sources(markers=[earlier, later]))
    assert [p.number for p in view.open_prs] == [4]
    assert [p.number for p in view.closed_prs] == [3]


def test_landed_repos_keeps_the_newest_written_sha_per_repo() -> None:
    landed = LandedRepos.of(
        [
            marker("merged/acme/one", "old-sha"),
            marker("delivery-pr/acme/one", "{}"),
            marker("merged/acme/two", " two-sha\n"),
            marker("merged/acme/one", "new-sha", epoch=2),
        ]
    )
    assert landed.shas == {"acme/one": "new-sha", "acme/two": "two-sha"}
    assert landed.names == {"acme/one", "acme/two"}


def test_blank_marker_alone_is_no_landing_and_leaves_the_pr_open() -> None:
    view = DeliveryRead.of(
        ChunkFacts(minted=True),
        delivery_sources(
            markers=[
                pr("delivery-pr/acme/one/1", '{"repo":"acme/one","number":1,"url":"http://forge/acme/one/pull/1"}'),
                marker("merged/acme/one", ""),
                marker("merged/acme/two", "abc123"),
            ]
        ),
    )
    assert [r.repo for r in view.landed_repos] == ["acme/two"]
    assert [p.number for p in view.open_prs] == [1]
    assert view.closed_prs == []


def test_blank_marker_written_after_a_sha_leaves_the_earlier_sha_standing() -> None:
    landed = LandedRepos.of([marker("merged/acme/one", "sha-1"), marker("merged/acme/one", " \n", epoch=2)])
    assert landed.shas == {"acme/one": "sha-1"}
    assert landed.names == {"acme/one"}
    assert LandedRepos.of([marker("merged/acme/one", "")]).names == frozenset()


def test_blank_legacy_landed_entry_is_dropped() -> None:
    view = DeliveryRead.of(
        ChunkFacts(minted=True), delivery_sources(legacy_landed={"acme/one": "", "acme/two": "sha-2"})
    )
    assert [r.repo for r in view.landed_repos] == ["acme/two"]


def test_no_code_resolution_relegates_earlier_pr_to_history_without_inventing_a_landing() -> None:
    declaration = {
        "repo": "acme/one",
        "record": "re_one",
        "base_branch": "main",
        "branch": "feat/x",
        "commit": "submitted",
    }
    name, data = no_code_marker(declaration)
    view = DeliveryRead.of(
        ChunkFacts(minted=True),
        delivery_sources(
            markers=[
                pr("delivery-pr/acme/one/1", '{"repo":"acme/one","number":1,"url":"http://forge/acme/one/pull/1"}'),
                marker(name, data),
            ]
        ),
        [declaration],
    )
    assert view.open_prs == []
    assert [p.number for p in view.closed_prs] == [1]
    assert view.landed_repos == []
    assert not view.awaiting_external_merge


def test_a_later_pr_is_not_hidden_by_an_old_no_code_resolution() -> None:
    declaration = {
        "repo": "acme/one",
        "record": "re_one",
        "base_branch": "main",
        "branch": "feat/x",
        "commit": "submitted",
    }
    name, data = no_code_marker(declaration)
    view = DeliveryRead.of(
        ChunkFacts(minted=True),
        delivery_sources(
            markers=[
                marker(name, data),
                pr(
                    "delivery-pr/acme/one/2",
                    '{"repo":"acme/one","number":2,"url":"http://forge/acme/one/pull/2"}',
                    epoch=2,
                ),
            ]
        ),
        [declaration],
    )
    assert [p.number for p in view.open_prs] == [2]


@pytest.mark.parametrize("key", ["record", "base_branch", "branch", "commit", "repo"])
def test_obsolete_no_code_does_not_hide_pr(key: str) -> None:
    declaration = {
        "repo": "acme/one",
        "record": "re_one",
        "base_branch": "main",
        "branch": "feat/x",
        "commit": "submitted",
    }
    name, data = no_code_marker(declaration)
    sources = delivery_sources(
        markers=[
            pr("delivery-pr/acme/one/1", '{"repo":"acme/one","number":1,"url":"http://forge/acme/one/pull/1"}'),
            marker(name, data),
            marker("awaiting-external-merge", "wait"),
        ]
    )
    view = DeliveryRead.of(ChunkFacts(minted=True), sources, [{**declaration, key: "changed"}])
    assert [p.number for p in view.open_prs] == [1]
    assert view.awaiting_external_merge
    assert not view.landed_repos

"""The generic hub command node primitive (#65) — unit + component tiers.

Unit tier: graph validation plus the pure poll-policy and env-injection helpers. Component
tier: the executor taking actions' passes over a real hub store, including the
serialization barrier every parked chunk must honor.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TypedDict, Unpack

import pytest
import yaml

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.actions.visit import GitCommits, UnconvergedDeliveryError
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import (
    ChunkFacts,
    HubNodePollFact,
    TransitionFact,
)
from blizzard.hub.domain.delivery.flows import DEFAULT_POLL_INTERVAL, DEFAULT_POLL_TIMEOUT, PollPolicy
from blizzard.hub.domain.graph.authoring import Reification
from blizzard.hub.domain.graph.model import GraphDoc
from blizzard.hub.domain.graph.validation import Validator
from tests.hub_command_graphs import (
    HUB_CMD_GRAPH_YAML,
)
from tests.support import (
    TEST_CATALOGUE,
)

# Unit — validation


def _errors(yaml_nodes: dict[str, object]) -> list[str]:
    doc = GraphDoc.of({"name": "g", "entry": "build", "nodes": yaml_nodes})
    return Validator.of(doc, TEST_CATALOGUE).result.errors


def test_hub_node_naming_an_action_and_choices_is_valid() -> None:
    doc = GraphDoc.of(
        {
            "name": "g",
            "entry": "build",
            "nodes": {
                "build": {
                    "executor": "runner",
                    "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
                },
                "merge": {
                    "executor": "hub",
                    "action": "test.answer",
                    "with": {"choice": ["success"]},
                    "judgement": {
                        "choices": {
                            "success": {"description": "ok", "to": "done"},
                            "failure": {"description": "bad", "to": "build"},
                        }
                    },
                },
            },
        }
    )
    result = Validator.of(doc, TEST_CATALOGUE).result
    assert result.errors == []


def test_hub_node_naming_an_action_rejects_prompt() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
            },
            "merge": {
                "executor": "hub",
                "prompt": "not allowed",
                "action": "test.answer",
                "with": {"choice": ["success"]},
                "judgement": {"choices": {"success": {"description": "ok", "to": "done"}}},
            },
        }
    )
    assert any("must not declare `prompt`" in e for e in errors)


def test_hub_node_naming_an_action_rejects_checks() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
            },
            "merge": {
                "executor": "hub",
                "checks": ["mise run test"],
                "action": "test.answer",
                "with": {"choice": ["success"]},
                "judgement": {"choices": {"success": {"description": "ok", "to": "done"}}},
            },
        }
    )
    assert any("must not declare `checks`" in e for e in errors)


def test_hub_node_naming_an_action_rejects_judgement_prompt() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
            },
            "merge": {
                "executor": "hub",
                "action": "test.answer",
                "with": {"choice": ["success"]},
                "judgement": {"prompt": "assess it", "choices": {"success": {"description": "ok", "to": "done"}}},
            },
        }
    )
    assert any("must not declare `judgement.prompt`" in e for e in errors)


def test_hub_node_naming_an_action_requires_a_judgement() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
            },
            "merge": {"executor": "hub", "action": "test.answer", "with": {"choice": ["success"]}},
        }
    )
    assert any("must declare a judgement" in e for e in errors)


def test_a_bare_hub_node_with_no_action_and_no_judgement_is_rejected() -> None:
    """An ``executor: hub`` node must declare a judgement (its outcome choices)
    whether or not it also names an ``action:`` — no node name is privileged (#67)."""
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "deliver"}}},
            },
            "deliver": {"executor": "hub"},
        }
    )
    assert any("must declare a judgement" in e for e in errors)


# Unit — pending outcome (#66)


_BUILD_TO_MERGE = {
    "executor": "runner",
    "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "merge"}}},
}


class _MergeNodeFields(TypedDict, total=False):
    poll_interval: int
    poll_timeout: int


def _merge_with(**overrides: Unpack[_MergeNodeFields]) -> dict[str, object]:
    node: dict[str, object] = {
        "executor": "hub",
        "action": "test.sequence",
        "with": {"choices": ["pending", "success"]},
        "judgement": {
            "choices": {
                "success": {"description": "ok", "to": "done"},
                "failure": {"description": "bad", "to": "build"},
            }
        },
    }
    node.update(overrides)
    return node


def test_poll_interval_and_timeout_are_legal_only_on_a_hub_command_node() -> None:
    errors = _errors(
        {
            "build": {
                "executor": "runner",
                "poll_interval": 30,
                "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "done"}}},
            },
        }
    )
    assert any("only legal on a hub command node" in e for e in errors)


def test_poll_interval_must_be_positive() -> None:
    errors = _errors(
        {
            "build": _BUILD_TO_MERGE,
            "merge": _merge_with(poll_interval=0),
        }
    )
    assert any("`poll_interval` must be a positive number of seconds" in e for e in errors)


def test_poll_timeout_must_be_positive() -> None:
    errors = _errors(
        {
            "build": _BUILD_TO_MERGE,
            "merge": _merge_with(poll_timeout=-1),
        }
    )
    assert any("`poll_timeout` must be a positive number of seconds" in e for e in errors)


def test_poll_timeout_must_be_at_least_poll_interval() -> None:
    errors = _errors(
        {
            "build": _BUILD_TO_MERGE,
            "merge": _merge_with(poll_interval=60, poll_timeout=30),
        }
    )
    assert any("`poll_timeout` must be >= `poll_interval`" in e for e in errors)


def test_poll_interval_and_timeout_are_valid_when_well_formed() -> None:
    errors = _errors(
        {
            "build": _BUILD_TO_MERGE,
            "merge": _merge_with(poll_interval=30, poll_timeout=600),
        }
    )
    assert errors == []


def test_poll_policy_defaults_when_unauthored() -> None:
    _, merge_node = _reified_merge_node()
    assert PollPolicy.of(merge_node).interval == DEFAULT_POLL_INTERVAL
    assert PollPolicy.of(merge_node).timeout == DEFAULT_POLL_TIMEOUT


def test_poll_policy_honors_the_authored_override() -> None:
    doc = GraphDoc.of(
        {
            "name": "g",
            "entry": "merge",
            "nodes": {"merge": _merge_with(poll_interval=15, poll_timeout=90)},
        }
    )
    graph = Reification.of(doc, FixedClock(datetime(2026, 7, 17, tzinfo=UTC))).graph
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    assert PollPolicy.of(merge_node).interval == timedelta(seconds=15)
    assert PollPolicy.of(merge_node).timeout == timedelta(seconds=90)


def _facts_at_hub_node(
    *, node_id: str = "nd_merge", epoch: int = 1, polls: list[HubNodePollFact] | None = None
) -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        transitions=[
            TransitionFact(
                to_node_id=node_id,
                to_node_executor=Executor.HUB,
                epoch=epoch,
                recorded_at=datetime(2026, 7, 17, tzinfo=UTC),
            )
        ],
        hub_node_polls=polls or [],
    )


def test_hub_node_pending_is_none_with_no_poll_fact() -> None:
    assert _facts_at_hub_node(polls=[]).hub_node_pending() is None


def test_pending_timeout_starts_at_the_latest_uninterrupted_pending_run() -> None:
    at = datetime(2026, 7, 17, tzinfo=UTC)
    polls = [
        HubNodePollFact("nd_merge", 1, at, kind="pending"),
        HubNodePollFact("nd_merge", 1, at + timedelta(hours=4), kind="awaiting-merge"),
        HubNodePollFact("nd_merge", 1, at + timedelta(hours=5), kind="pending"),
        HubNodePollFact("nd_merge", 1, at + timedelta(hours=6), kind="pending"),
    ]
    facts = _facts_at_hub_node(polls=polls)
    assert facts.hub_node_pending_since(node_id="nd_merge", epoch=1) == at + timedelta(hours=5)


def test_hub_node_pending_returns_the_newest_matching_poll_fact() -> None:
    facts = _facts_at_hub_node(
        polls=[
            HubNodePollFact(node_id="nd_merge", epoch=1, polled_at=datetime(2026, 7, 17, 0, 0, tzinfo=UTC)),
            HubNodePollFact(node_id="nd_merge", epoch=1, polled_at=datetime(2026, 7, 17, 0, 1, tzinfo=UTC)),
        ]
    )
    pending = facts.hub_node_pending()
    assert pending is not None
    assert pending.polled_at == datetime(2026, 7, 17, 0, 1, tzinfo=UTC)


def test_hub_node_pending_ignores_a_poll_fact_from_a_stale_epoch() -> None:
    """A poll fact recorded for an earlier visit to the SAME node (before a later
    transition re-entered it) must not read as pending now — epoch-keyed, not
    node-keyed alone."""
    stale_poll = HubNodePollFact(node_id="nd_merge", epoch=1, polled_at=datetime(2026, 7, 17, tzinfo=UTC))
    facts = _facts_at_hub_node(epoch=2, polls=[stale_poll])
    assert facts.hub_node_pending() is None


def test_hub_node_pending_is_none_off_a_non_hub_node() -> None:
    facts = ChunkFacts(
        minted=True,
        transitions=[
            TransitionFact(
                to_node_id="nd_build",
                to_node_executor=Executor.RUNNER,
                epoch=1,
                recorded_at=datetime(2026, 7, 17, tzinfo=UTC),
            )
        ],
        hub_node_polls=[HubNodePollFact(node_id="nd_build", epoch=1, polled_at=datetime(2026, 7, 17, tzinfo=UTC))],
    )
    assert facts.hub_node_pending() is None


# Unit — env injection


def _reified_merge_node():
    doc = GraphDoc.of({"name": "g", "entry": "build", "nodes": _yaml_nodes()})
    graph = Reification.of(doc, FixedClock(datetime(2026, 7, 17, tzinfo=UTC))).graph
    merge_node = graph.node_by_name("merge")
    assert merge_node is not None
    return graph, merge_node


def _yaml_nodes() -> dict[str, object]:
    return yaml.safe_load(HUB_CMD_GRAPH_YAML)["nodes"]


def _commit_row(
    *,
    node_name: str,
    epoch: int,
    commit: str,
    repo: str = "acme/widget",
    branch: str = "feat/x",
    forge: str | None = None,
) -> StoredArtifact:
    """One repo's declared git pointer, as the node at ``epoch`` recorded it.

    ``forge`` defaults to the origin the repo's own name implies, so a fixture naming two
    repos describes two distinct origins."""
    return StoredArtifact(
        kind=ArtifactKind.GIT_COMMIT,
        name=repo.rpartition("/")[2],  # named after the repo, never the literal produces name
        data=f"{branch}:{commit}",
        repo=repo,
        forge=forge if forge is not None else f"https://github.com/{repo}",
        artifact_id=f"art_{node_name}_{epoch}",
        chunk_id="ch_x",
        node_id=f"nd_{node_name}",
        node_name=node_name,
        epoch=epoch,
    )


def _delivered(artifacts: list[StoredArtifact]) -> list[dict[str, str | None]]:
    """The commits a visit over ``artifacts`` delivers, one per repository."""
    return GitCommits.of(artifacts).payload()


def test_a_rebased_tip_supersedes_the_pre_rebase_commit_for_the_same_repo() -> None:
    """The pre-push wedge (#130): a rebase REWRITES the branch, so `build`'s orphaned
    commit can never fast-forward. Delivery must see ONLY the rebased tip — keyed per
    ``(node_name, name)``, the later declaration wins."""
    commits = _delivered(
        [
            _commit_row(node_name="build", epoch=2, commit="a" * 40),
            _commit_row(node_name="pre-push", epoch=7, commit="b" * 40),
        ]
    )
    assert commits == [{"repo": "acme/widget", "branch": "feat/x", "commit": "b" * 40}]


def test_declaration_order_never_decides_which_commit_delivers() -> None:
    """Epoch decides, not row order — the store's ordering must not be load-bearing."""
    rows = [
        _commit_row(node_name="pre-push", epoch=7, commit="b" * 40),
        _commit_row(node_name="build", epoch=2, commit="a" * 40),
    ]
    assert _delivered(rows) == [{"repo": "acme/widget", "branch": "feat/x", "commit": "b" * 40}]


def test_a_multi_repo_chunk_keeps_one_commit_per_repo() -> None:
    """Collapsing is per repo, so a chunk spanning repos still delivers all of them."""
    commits = _delivered(
        [
            _commit_row(node_name="build", epoch=2, commit="a" * 40, repo="acme/widget"),
            _commit_row(node_name="build", epoch=2, commit="c" * 40, repo="acme/gadget"),
            _commit_row(node_name="pre-push", epoch=7, commit="b" * 40, repo="acme/widget"),
        ]
    )
    by_repo = {c["repo"]: c["commit"] for c in commits}
    assert by_repo == {"acme/widget": "b" * 40, "acme/gadget": "c" * 40}


# Component — pending outcome (#66): polls without blocking the queue


def test_a_later_epoch_supersedes_an_earlier_pointer_for_the_same_repo() -> None:
    """The rebase case the per-repo key exists for: pre-push rewrites build's branch, so
    the later declaration replaces it rather than delivery being handed both."""
    commits = _delivered(
        [
            _commit_row(node_name="build", epoch=1, commit="a" * 40),
            _commit_row(node_name="pre-push", epoch=3, commit="b" * 40),
        ]
    )

    assert commits == [{"repo": "acme/widget", "branch": "feat/x", "commit": "b" * 40}]


def test_an_identical_re_declaration_at_one_epoch_is_a_correction_not_a_conflict() -> None:
    commits = _delivered(
        [
            _commit_row(node_name="build", epoch=2, commit="a" * 40),
            _commit_row(node_name="build", epoch=2, commit="a" * 40),
        ]
    )

    assert commits == [{"repo": "acme/widget", "branch": "feat/x", "commit": "a" * 40}]


def test_two_branches_for_one_repo_at_one_epoch_refuse_to_deliver() -> None:
    """A chunk declaring a branch per env at the same epoch is unconverged: keeping
    whichever came last would deliver a fraction of the work and call it a landing, so
    it is refused, not tie-broken. Convergence belongs to pre-push."""
    with pytest.raises(UnconvergedDeliveryError) as exc:
        _delivered(
            [
                _commit_row(node_name="build", epoch=2, commit="a" * 40, branch="feat/from-e1"),
                _commit_row(node_name="build", epoch=2, commit="b" * 40, branch="feat/from-e2"),
            ]
        )

    assert "acme/widget" in str(exc.value)
    assert "rolled up" in str(exc.value)


def test_delivery_addresses_a_repo_by_the_owner_its_own_origin_names() -> None:
    """Two repos under two owners in one chunk: each is addressed by the coordinate its
    own origin encodes, rather than both being re-qualified with one workspace-wide
    owner — which could only ever be right for one of them."""
    commits = _delivered(
        [
            _commit_row(node_name="build", epoch=1, commit="a" * 40, repo="acme/widget"),
            _commit_row(node_name="build", epoch=1, commit="b" * 40, repo="other-org/gadget"),
        ]
    )

    assert {c["repo"] for c in commits} == {"acme/widget", "other-org/gadget"}


def test_delivery_falls_back_to_the_bare_name_for_an_origin_that_names_no_owner() -> None:
    """The verification forge fronts flat bare origins that resolve under any owner, so
    an unqualifiable origin must leave the bare name rather than inventing a
    coordinate."""
    commits = _delivered(
        [
            _commit_row(
                node_name="build",
                epoch=1,
                commit="a" * 40,
                repo="toy-api",
                forge="file:///origins/toy-api.git",
            )
        ]
    )

    assert commits == [{"repo": "toy-api", "branch": "feat/x", "commit": "a" * 40}]


def test_the_same_name_under_two_owners_stays_two_repositories() -> None:
    """Production rows carry the bare manifest name in ``repo`` and the origin in ``forge``;
    identity is read from the origin, so neither owner's pointer merges into or supersedes
    the other's — at one epoch or across epochs."""
    at_one_epoch = _delivered(
        [
            _commit_row(
                node_name="build", epoch=1, commit="a" * 40, repo="widget", forge="https://github.com/owner-a/widget"
            ),
            _commit_row(
                node_name="build", epoch=1, commit="b" * 40, repo="widget", forge="https://github.com/owner-b/widget"
            ),
        ]
    )
    across_epochs = _delivered(
        [
            _commit_row(
                node_name="build", epoch=1, commit="a" * 40, repo="widget", forge="https://github.com/owner-a/widget"
            ),
            _commit_row(
                node_name="build", epoch=2, commit="b" * 40, repo="widget", forge="https://github.com/owner-b/widget"
            ),
        ]
    )

    assert {c["repo"] for c in at_one_epoch} == {"owner-a/widget", "owner-b/widget"}
    assert {c["repo"] for c in across_epochs} == {"owner-a/widget", "owner-b/widget"}


def test_one_identity_declared_twice_with_different_commits_refuses_to_deliver() -> None:
    with pytest.raises(UnconvergedDeliveryError) as exc:
        _delivered(
            [
                _commit_row(
                    node_name="build", epoch=1, commit="a" * 40, repo="widget", forge="git@github.com:acme/widget.git"
                ),
                _commit_row(
                    node_name="build", epoch=1, commit="b" * 40, repo="widget", forge="https://github.com/acme/widget"
                ),
            ]
        )

    assert "acme/widget" in str(exc.value)


def test_declares_git_commit_reads_the_graphs_own_intent() -> None:
    """The signal that tells a lost commit apart from one never promised. Read off the
    graph rather than the artifacts, so a code graph that produced nothing still counts
    as expecting something — which is the whole case being caught."""
    graph, _ = _reified_merge_node()

    declares = graph.declares_git_commit

    assert declares is any(spec.kind is ArtifactKind.GIT_COMMIT for node in graph.nodes for spec in node.produces)

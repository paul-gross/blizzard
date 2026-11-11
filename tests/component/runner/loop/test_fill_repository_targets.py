"""A chunk's repository targets through FILL: the peek opts in only when every bound workspace prepares at targets,
the acquire is handed the peeked targets, the binding records them, the claim names what the binding recorded, a
``targets`` refusal gives the environment back, and the spawned worker's facts table names each targeted repository
present in its environment — on a reclaim too, read from the binding. Driven against a real (tmp sqlite) runner store
with fakes at the seams."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from blizzard.foundation.claim_denials import ClaimDenialReason
from blizzard.foundation.clock import FixedClock
from blizzard.runner.environments.provider import RepositoryTarget
from blizzard.runner.environments.workspaces import WorkspaceEntry, WorkspaceRegistry
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.hub.client import (
    BindingDeclaration,
    ClaimRequest,
    IncompatibleDenial,
    QueueEntry,
    RouteClaimOutcome,
)
from blizzard.runner.lifecycle.claim import InterruptedClaims
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.loop.steps import Fill
from tests.support.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
)

_NOW = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]
#: The environment's worktrees: ``acme/api`` is targeted, ``acme/web`` is not.
_REPOS: dict[str, Sequence[tuple[str, str]]] = {
    "a1": [("api", "git@github.com:acme/api.git"), ("web", "git@github.com:acme/web.git")]
}
_API = RepositoryTarget("repo_api", "api", "acme", "api", "rust-rewrite", inherited=False)
_ELSEWHERE = RepositoryTarget("repo_lib", "lib", "acme", "lib", "main", inherited=True)


class _Hub(FakeHub):
    """A fake hub answering each claim with ``outcome``, or a win carrying the chunk's own envelope."""

    def __init__(self, outcome: RouteClaimOutcome | None = None) -> None:
        super().__init__()
        self._outcome = outcome

    def claim_route(self, claim: ClaimRequest) -> RouteClaimOutcome:
        self.claim_outcome = self._outcome or claimed_outcome(
            claim.chunk_id, make_envelope(claim.chunk_id, "build", node_id="nd_build", choices=_CHOICES)
        )
        return super().claim_route(claim)


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _ctx(
    store: SqlAlchemyRunnerStore, hub: FakeHub, provider: FakeProvider, harness: FakeHarness | None = None
) -> LoopContext:
    return make_context(
        store,
        hub=hub,
        provider=FakeProvider({}),
        harness=harness or FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=LoopConfig(
            runner_name="r1", runner_id=FIXTURE_RUNNER_ID, bindings=(BindingDeclaration("pr_a", "ws_a"),), agents=1
        ),
        workspaces=WorkspaceRegistry([WorkspaceEntry("ws_a", provider, spawn_root="/root-a")]),
    )


def _provider(*, honors_targets: bool = True) -> FakeProvider:
    return FakeProvider({"a1": "/root-a/a1"}, repos=_REPOS, honors_targets=honors_targets)


def _entry(targets: tuple[RepositoryTarget, ...] | None) -> QueueEntry:
    return QueueEntry(chunk_id="ch_1", graph_id="gr_1", position=0, project_id="pr_a", repository_targets=targets)


def test_fill_prepares_binds_and_claims_at_the_peeked_targets_and_names_them_to_the_worker(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hub, provider, harness = _Hub(), _provider(), FakeHarness(handle=_HANDLE, verdict="pass")
    hub.queue = [_entry((_API, _ELSEWHERE))]

    Fill(_ctx(store, hub, provider, harness)).run()

    assert [call.repository_targets for call in hub.peek_queue_requests] == [True]
    assert provider.acquired_targets == [(_API, _ELSEWHERE)]
    assert store.held_targets("ch_1") == (_API, _ELSEWHERE)
    assert [claim.repository_targets for claim in hub.claims] == [(_API, _ELSEWHERE)]
    prefix = harness.spawns[0][1].prompt_prefix
    assert "| repository acme/api base | `rust-rewrite` |" in prefix
    assert "repository acme/lib" not in prefix, "a target with no worktree in the environment is not named"
    assert "repository acme/web" not in prefix, "an untargeted worktree stays on the workspace's branch"


def test_a_workspace_that_cannot_prepare_at_targets_keeps_the_peek_from_opting_in(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hub = _Hub()
    hub.queue = [_entry(None)]

    Fill(_ctx(store, hub, _provider(honors_targets=False))).run()

    assert [call.repository_targets for call in hub.peek_queue_requests] == [False]
    assert [claim.repository_targets for claim in hub.claims] == [None], "an entry naming no targets claims naming none"


def test_a_targets_refusal_gives_the_environment_back(tmp_path: Path) -> None:
    store = _store(tmp_path)
    denial = IncompatibleDenial(chunk_id="ch_1", incompatible_runner_id="r1", reason=ClaimDenialReason.TARGETS)
    hub, provider = _Hub(RouteClaimOutcome(denied_incompatible=denial)), _provider()
    hub.queue = [_entry((_API,))]

    Fill(_ctx(store, hub, provider)).run()

    assert [claim.repository_targets for claim in hub.claims] == [(_API,)]
    assert store.held_bindings() == []
    assert provider.released == ["a1"]


def test_a_reclaim_names_and_spawns_with_the_targets_the_binding_recorded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_binding(
        chunk_id="ch_1",
        environment_id="a1",
        workdir="/root-a/a1",
        bound_at=_NOW,
        workspace_id="ws_a",
        project_id="pr_a",
        targets=[_API],
    )
    hub, harness = _Hub(), FakeHarness(handle=_HANDLE, verdict="pass")

    InterruptedClaims(_ctx(store, hub, _provider(), harness)).reclaim("ch_1", store.bindings_for_chunk("ch_1"))

    assert [claim.repository_targets for claim in hub.claims] == [(_API,)]
    assert "| repository acme/api base | `rust-rewrite` |" in harness.spawns[0][1].prompt_prefix

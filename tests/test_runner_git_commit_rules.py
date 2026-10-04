"""The git-commit declaration and confirmation rules, pinned by value — no store, no clock,
no git, no provider.

Resolving the declaring environment, refusing an unlisted repo or a late declaration, the
record a declaration writes, and confirming and converging declared pointers.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.repo_ref import repo_identity
from blizzard.foundation.tokens import TokenHash
from blizzard.runner.environments.worktree import WorktreeGitError
from blizzard.runner.leases import Lease
from blizzard.runner.leases.lease_auth import LeaseToken, LeaseTokenRejected
from blizzard.runner.leases.worker_lease import WorkerLease
from blizzard.runner.lifecycle.judgement.git_commit_declaration import (
    GitCommitDeclaration,
    GitCommitDeclarationRecord,
    GitCommitDeclarationTooLate,
    GitCommitDeclarationUnknownRepo,
    require_listed_repo,
    resolve_declaring_environment,
)
from blizzard.runner.lifecycle.judgement.git_commits import (
    CommandFailure,
    PointerGroup,
    confirm_declaration,
    converge_pointers,
    resolve_origin,
)
from blizzard.wire.completion import SubmittedArtifact

pytestmark = pytest.mark.unit

_AT = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)
_ORIGIN = "git@github.com:acme/toy-api.git"
_LEASE = Lease(
    lease_id="lease_1",
    chunk_id="ch_1",
    graph_id="gr_1",
    node_id="nd_build",
    node_name="build",
    epoch=3,
    runner_id="runner-local",
    retries_max=2,
    created_at=_AT,
)
_DECLARATION = GitCommitDeclaration(environment_id="e1", repo="toy-api", branch="feat/x", commit="abc123")


def _declared(*, active: bool = True, **overrides: object) -> GitCommitDeclarationRecord:
    fields: dict[str, object] = {"known_repos": ["toy-api"], "outcome_pending": False, "at": _AT}
    fields.update(overrides)
    return _DECLARATION.declared_by(WorkerLease(lease=_LEASE, active=active), **fields)  # type: ignore[arg-type]


# --- authorization ------------------------------------------------------------------------


def test_declared_refuses_invalid_token() -> None:
    stored = TokenHash("the-lease-token").hex
    LeaseToken("the-lease-token", stored).require("lease_1")
    for presented in ("a-wrong-token", None):
        with pytest.raises(LeaseTokenRejected, match="presented token does not authorize lease lease_1"):
            LeaseToken(presented, stored).require("lease_1")


# --- the declaring environment ------------------------------------------------------------


def test_env_single_bound() -> None:
    assert resolve_declaring_environment("ch_1", ["e1"], None) == "e1"


def test_env_named_bound() -> None:
    assert resolve_declaring_environment("ch_1", ["e1", "e2"], "e2") == "e2"


def test_env_none_bound_refused() -> None:
    with pytest.raises(GitCommitDeclarationUnknownRepo, match="chunk ch_1 holds no environment to declare against"):
        resolve_declaring_environment("ch_1", [], None)


def test_env_several_need_flag() -> None:
    with pytest.raises(GitCommitDeclarationUnknownRepo, match=r"holds \['e1', 'e2'\] — pass `--env`"):
        resolve_declaring_environment("ch_1", ["e2", "e1"], None)


def test_env_named_unbound_refused() -> None:
    with pytest.raises(GitCommitDeclarationUnknownRepo, match=r"does not hold environment 'e9' — it holds \['e1'\]"):
        resolve_declaring_environment("ch_1", ["e1"], "e9")


# --- the declaration record ---------------------------------------------------------------


def test_unlisted_repo_refused() -> None:
    with pytest.raises(
        GitCommitDeclarationUnknownRepo, match=r"environment 'e1' has no repo 'web' — it holds \['a', 'b'\]"
    ):
        require_listed_repo("e1", "web", ["b", "a"])
    with pytest.raises(GitCommitDeclarationUnknownRepo):
        _declared(known_repos=["other"])


def test_declared_carries_lease_chunk_node_epoch() -> None:
    assert _declared() == GitCommitDeclarationRecord(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=3,
        environment_id="e1",
        repo="toy-api",
        branch="feat/x",
        commit="abc123",
        declared_at=_AT,
        rides_completion=True,
    )


def test_declared_after_outcome_pending_too_late() -> None:
    with pytest.raises(GitCommitDeclarationTooLate, match="lease lease_1's outcome is already buffered") as refused:
        _declared(outcome_pending=True)
    assert refused.value.lease_id == "lease_1"


def test_closed_reference_lease_accepts_the_declaration_but_rides_no_completion() -> None:
    assert _declared(active=False).rides_completion is False


# --- confirming a declaration -------------------------------------------------------------


def test_confirm_no_origin_manifest_changed() -> None:
    failure = resolve_origin(("e1", "toy-api"), {("e1", "web"): "o-web", ("e2", "api"): "o-api"})
    assert failure == CommandFailure(
        command="resolve origin for --repo 'toy-api' in environment 'e1'",
        stderr_tail="environment 'e1' no longer lists repo 'toy-api'; it lists ['web']",
    )


def test_resolve_origin_finds_the_manifest_origin() -> None:
    assert resolve_origin(("e1", "toy-api"), {("e1", "toy-api"): _ORIGIN}) == _ORIGIN


def test_confirm_reports_a_probe_error() -> None:
    outcome = confirm_declaration(("e1", "toy-api"), _DECLARATION, _ORIGIN, WorktreeGitError("network down"))
    assert outcome == CommandFailure(
        command=f"git ls-remote {_ORIGIN} feat/x (--repo 'toy-api', --env 'e1')", stderr_tail="network down"
    )


def test_confirm_not_head_hints_push() -> None:
    outcome = confirm_declaration(("e1", "toy-api"), _DECLARATION, _ORIGIN, False)
    assert isinstance(outcome, CommandFailure)
    assert outcome.stderr_tail == (
        f"declared commit abc123 is not what branch 'feat/x' points at on {_ORIGIN} — push the branch "
        "(or re-declare the sha `git rev-parse HEAD` actually produced) and declare it again"
    )


def test_confirm_ok_names_repo_identity() -> None:
    assert confirm_declaration(("e1", "toy-api"), _DECLARATION, _ORIGIN, True) == SubmittedArtifact(
        name=repo_identity(_ORIGIN, "toy-api"),
        kind=ArtifactKind.GIT_COMMIT,
        forge=_ORIGIN,
        repo="toy-api",
        branch_name="feat/x",
        commit_hash="abc123",
    )


# --- converging pointers ------------------------------------------------------------------


def _pointer(branch: str, commit: str, *, name: str = "acme/toy-api") -> SubmittedArtifact:
    return SubmittedArtifact(
        name=name, kind=ArtifactKind.GIT_COMMIT, forge=_ORIGIN, repo="toy-api", branch_name=branch, commit_hash=commit
    )


def test_converge_dedupes() -> None:
    same = _pointer("feat/x", "abc123")
    groups = converge_pointers({("e1", "toy-api"): same, ("e2", "toy-api"): _pointer("feat/x", "abc123")})
    assert groups == [PointerGroup(identity="acme/toy-api", pointers=(same,), disagreement=None)]


def test_converge_disagreement_submits_all() -> None:
    a, b = _pointer("feat/x", "abc123"), _pointer("feat/y", "def456")
    groups = converge_pointers({("e1", "toy-api"): a, ("e2", "toy-api"): b})
    assert groups == [
        PointerGroup(
            identity="acme/toy-api",
            pointers=(a, b),
            disagreement=CommandFailure(
                command="converge commit pointers for repository 'acme/toy-api'",
                stderr_tail="environments declare different pointers: 'e1' -> feat/x@abc123; 'e2' -> feat/y@def456",
            ),
        )
    ]


def test_converge_keeps_repositories_apart() -> None:
    a, b = _pointer("feat/x", "abc123"), _pointer("feat/x", "abc123", name="acme/web")
    groups = converge_pointers({("e1", "toy-api"): a, ("e1", "web"): b})
    assert [(g.identity, g.pointers, g.disagreement) for g in groups] == [
        ("acme/toy-api", (a,), None),
        ("acme/web", (b,), None),
    ]

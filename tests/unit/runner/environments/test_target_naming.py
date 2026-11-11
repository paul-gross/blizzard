"""Which worktree a repository target names — matched by its origin's ``owner/repo`` (unit tier)."""

from __future__ import annotations

from blizzard.runner.environments.provider import RepositoryTarget

_TARGET = RepositoryTarget("repo_x", "x", "Paul-Gross", "x", "rust-rewrite", inherited=False)


def test_a_target_names_a_worktree_whose_origin_spells_its_owner_in_another_case() -> None:
    assert _TARGET.names("git@github.com:paul-gross/x.git", "x")


def test_a_target_does_not_name_a_worktree_of_another_repository_or_owner() -> None:
    assert not _TARGET.names("git@github.com:paul-gross/y.git", "y")
    assert not _TARGET.names("git@github.com:acme/x.git", "x")

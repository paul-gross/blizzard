"""A rewrite is admitted one complete tree transition at a time."""

from dataclasses import replace

from blizzard.hub.delivery.actions.landing.rewrite import Commit, RewritePlan, TreeEntry, prepare, proves


def entry(path: str, content: bytes) -> TreeEntry:
    return TreeEntry(path, "100644", content.hex(), content)


def commit(oid: str, parent: str, *entries: TreeEntry) -> Commit:
    return Commit(oid, (parent,), entries, "author", "message")


def test_independent_files_and_ordered_chain() -> None:
    root = commit("root", "older", entry("a", b"old"))
    first = commit("one", "root", entry("a", b"new"))
    second = commit("two", "one", entry("a", b"new"), entry("b", b"added"))
    base = commit("base", "root", entry("a", b"old"), entry("c", b"upstream"))
    plan = prepare(root, (first, second), base)
    assert plan is not None
    rewritten = (
        commit("r1", "base", entry("a", b"new"), entry("c", b"upstream")),
        commit("r2", "r1", entry("a", b"new"), entry("b", b"added"), entry("c", b"upstream")),
    )
    assert proves(plan, rewritten)
    assert not proves(plan, rewritten[1:])
    assert not proves(plan, (replace(rewritten[0], message="other"), rewritten[1]))
    assert not proves(plan, (rewritten[0], replace(rewritten[1], parents=("base",))))


def test_nonoverlapping_text_replay_and_overlap_refusal() -> None:
    root = commit("root", "older", entry("a", b"one\ntwo\nthree\nfour\nfive\n"))
    source = commit("source", "root", entry("a", b"ONE\ntwo\nthree\nfour\nfive\n"))
    base = commit("base", "root", entry("a", b"one\ntwo\nthree\nfour\nFIVE\n"))
    plan = prepare(root, (source,), base)
    assert plan is not None
    merged = entry("a", b"ONE\ntwo\nthree\nfour\nFIVE\n")
    assert proves(plan, (commit("rewritten", "base", merged),))
    conflict = replace(base, tree=(entry("a", b"OTHER\ntwo\nthree\nfour\nfive\n"),))
    assert prepare(root, (source,), conflict) is None


def test_foreign_add_revert_cannot_hide_in_final_tree() -> None:
    root = commit("root", "older", entry("a", b"old"))
    source = commit("source", "root", entry("a", b"new"))
    plan = prepare(root, (source,), root)
    assert plan is not None
    foreign = commit("foreign", "root", entry("a", b"old"), entry("evil", b"x"))
    final = commit("final", "foreign", entry("a", b"new"))
    assert not proves(plan, (foreign, final))
    assert not proves(plan, (replace(final, parents=("root",), tree=(*final.tree, entry("evil", b"x"))),))


def test_empty_commitment_is_not_a_proof() -> None:
    assert not proves(RewritePlan("base", ()), ())


def test_incomplete_or_unsupported_source_is_refused() -> None:
    root = commit("root", "older", entry("a", b"old"))
    assert prepare(root, (), root) is None
    assert prepare(root, (commit("x", "wrong", entry("a", b"new")),), root) is None
    assert prepare(root, (commit("x", "root", entry("a", b"old")),), root) is None
    duplicate = commit("x", "root", entry("a", b"new"), entry("a", b"other"))
    assert prepare(root, (duplicate,), root) is None
    source = commit("x", "root", entry("a", b"new"))
    base = commit("base", "root", entry("a", b"\xff"))
    assert prepare(root, (source,), base) is None


def test_deletion_and_gitlink_require_exact_entries() -> None:
    link = TreeEntry("link", "160000", "object", None)
    root = commit("root", "older", entry("a", b"old"), link)
    source = commit("source", "root", TreeEntry("link", "160000", "other", None))
    base = commit("base", "root", entry("a", b"old"), link, entry("extra", b"upstream"))
    plan = prepare(root, (source,), base)
    assert plan is not None
    good = commit("r", "base", TreeEntry("link", "160000", "other", None), entry("extra", b"upstream"))
    assert proves(plan, (good,))
    assert not proves(plan, (replace(good, tree=(TreeEntry("link", "160000", "wrong", None), good.tree[1])),))


def test_unhydrated_identity_does_not_mask_modes_or_foreign_trees() -> None:
    old = TreeEntry("a", "100644", "old", None)
    new = TreeEntry("a", "100644", "new", None)
    root = commit("root", "older", old)
    source = commit("source", "root", new)
    plan = prepare(root, (source,), root)
    assert plan is not None
    assert proves(plan, (commit("r", "root", new),))
    assert not proves(plan, (commit("r", "root", replace(new, mode="100755")),))
    assert not proves(plan, (commit("r", "root", old),))
    assert not proves(plan, (commit("r", "root", new, TreeEntry("evil", "100644", "x", None)),))
    assert prepare(root, (commit("source", "root", old),), root) is None


def test_multicommit_text_replay_proves_each_intermediate_tree() -> None:
    root = commit("root", "older", entry("a", b"one\ntwo\nthree\nfour\nfive\n"))
    first = commit("first", "root", entry("a", b"ONE\ntwo\nthree\nfour\nfive\n"))
    second = commit("second", "first", entry("a", b"ONE\nTWO\nthree\nfour\nfive\n"))
    base = commit("base", "root", entry("a", b"one\ntwo\nthree\nfour\nFIVE\n"))
    plan = prepare(root, (first, second), base)
    assert plan is not None
    r1 = commit("r1", "base", entry("a", b"ONE\ntwo\nthree\nfour\nFIVE\n"))
    r2 = commit("r2", "r1", entry("a", b"ONE\nTWO\nthree\nfour\nFIVE\n"))
    assert proves(plan, (r1, r2))
    assert not proves(plan, (replace(r1, tree=(entry("a", b"foreign"),)), r2))
    assert not proves(plan, (replace(r1, parents=("other-base",)), r2))


def test_concurrent_mode_or_gitlink_change_is_not_text_replayed() -> None:
    root = commit("root", "older", TreeEntry("a", "100644", "old", None))
    source = commit("source", "root", TreeEntry("a", "100755", "old", None))
    base = commit("base", "root", TreeEntry("a", "100644", "other", None))
    assert prepare(root, (source,), base) is None
    root = commit("root", "older", TreeEntry("a", "160000", "old", None))
    source = commit("source", "root", TreeEntry("a", "160000", "new", None))
    base = commit("base", "root", TreeEntry("a", "160000", "other", None))
    assert prepare(root, (source,), base) is None


async def test_large_unchanged_tree_loads_only_changed_blobs_and_proves_replay() -> None:
    from collections.abc import Awaitable
    from types import SimpleNamespace
    from typing import TypeVar, cast
    from unittest.mock import AsyncMock

    from blizzard.hub.delivery.actions.landing.repo import RepoLanding
    from blizzard.hub.delivery.actions.landing.rewrite_admission import (
        _commit,  # pyright: ignore[reportPrivateUsage]  # Exercise evidence loading.
        _hydrate,  # pyright: ignore[reportPrivateUsage]  # Exercise evidence loading.
        _prepare,  # pyright: ignore[reportPrivateUsage]  # Exercise evidence loading.
    )

    unchanged = [SimpleNamespace(path=f"file-{i}", mode="100644", revision=f"same-{i}") for i in range(2000)]
    contents = {
        "old": b"one\ntwo\nthree\nfour\nfive\n",
        "work": b"ONE\ntwo\nthree\nfour\nfive\n",
        "base": b"one\ntwo\nthree\nfour\nFIVE\n",
        "replay": b"ONE\ntwo\nthree\nfour\nFIVE\n",
    }
    trees = {
        name: (*unchanged, SimpleNamespace(path="changed", mode="100644", revision=blob))
        for name, blob in (("root", "old"), ("source", "work"), ("base", "base"), ("rewritten", "replay"))
    }
    parents = {"root": ("older",), "source": ("root",), "base": ("root",), "rewritten": ("base",)}

    async def commit_evidence(oid: str) -> SimpleNamespace:
        return SimpleNamespace(
            revision=oid,
            tree=oid,
            parents=parents[oid],
            author_name="a",
            author_email="e",
            author_date="d",
            message="m",
        )

    async def tree_evidence(oid: str) -> tuple[SimpleNamespace, ...]:
        return trees[oid]

    async def blob_evidence(oid: str) -> bytes:
        return contents[oid]

    T = TypeVar("T")

    async def ask(value: Awaitable[T]) -> T:
        return await value

    blobs = AsyncMock(side_effect=blob_evidence)
    refs = SimpleNamespace(commit_evidence=commit_evidence, tree_evidence=tree_evidence, blob_evidence=blobs)
    repo = cast(RepoLanding, SimpleNamespace(forge=SimpleNamespace(refs=refs), ask=ask))
    plan = await _prepare(repo, "root", ("source",), "base")
    assert plan is not None
    assert {call.args[0] for call in blobs.call_args_list} == {"old", "work", "base"}
    rewritten = await _commit(repo, "rewritten")
    hydrated = await _hydrate(repo, (rewritten,), {"changed"})
    assert proves(plan, hydrated)
    assert not proves(plan, (rewritten,))
    assert blobs.await_count == 4
    foreign = replace(hydrated[0], tree=(*hydrated[0].tree, TreeEntry("foreign", "100644", "bad", None)))
    assert not proves(plan, (foreign,))


async def test_conflicting_mode_changes_do_not_fetch_blobs() -> None:
    from collections.abc import Awaitable
    from types import SimpleNamespace
    from typing import TypeVar, cast
    from unittest.mock import AsyncMock

    from blizzard.hub.delivery.actions.landing.repo import RepoLanding
    from blizzard.hub.delivery.actions.landing.rewrite_admission import (
        _prepare,  # pyright: ignore[reportPrivateUsage]  # Exercise evidence loading.
    )

    async def commit_evidence(oid: str) -> SimpleNamespace:
        return SimpleNamespace(
            revision=oid, tree=oid, parents=("root",), author_name="a", author_email="e", author_date="d", message="m"
        )

    async def tree_evidence(oid: str) -> tuple[SimpleNamespace, ...]:
        mode, blob = {"root": ("100644", "old"), "source": ("100755", "old"), "base": ("100644", "other")}[oid]
        return (SimpleNamespace(path="a", mode=mode, revision=blob),)

    T = TypeVar("T")

    async def ask(value: Awaitable[T]) -> T:
        return await value

    blobs = AsyncMock()
    refs = SimpleNamespace(commit_evidence=commit_evidence, tree_evidence=tree_evidence, blob_evidence=blobs)
    repo = cast(RepoLanding, SimpleNamespace(forge=SimpleNamespace(refs=refs), ask=ask))
    assert await _prepare(repo, "root", ("source",), "base") is None
    blobs.assert_not_awaited()

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

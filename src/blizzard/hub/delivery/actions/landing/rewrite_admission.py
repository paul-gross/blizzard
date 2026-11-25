"""Durable, declaration-bound authorization of automatic rebase updates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from itertools import pairwise
from typing import Any, cast

from blizzard.hub.delivery.actions.landing.head_gate import gate_head
from blizzard.hub.delivery.actions.landing.repo import RepoLanding
from blizzard.hub.delivery.actions.landing.rewrite import Commit, RewritePlan, TreeEntry, prepare, proves


async def _commit(repo: RepoLanding, oid: str) -> Commit:
    refs = repo.forge.refs
    evidence = await repo.ask(refs.commit_evidence(oid))
    entries = await repo.ask(refs.tree_evidence(evidence.tree))
    tree = tuple(TreeEntry(entry.path, entry.mode, entry.revision, None) for entry in entries)
    author = json.dumps([evidence.author_name, evidence.author_email, evidence.author_date])
    return Commit(evidence.revision, evidence.parents, tree, author, evidence.message)


async def _hydrate(repo: RepoLanding, commits: tuple[Commit, ...], paths: set[str]) -> tuple[Commit, ...]:
    """Load only relevant blobs, once per immutable object in this proof."""
    blobs: dict[str, bytes] = {}
    result: list[Commit] = []
    for commit in commits:
        entries: list[TreeEntry] = []
        for entry in commit.tree:
            if entry.path in paths and entry.mode != "160000":
                if entry.oid not in blobs:
                    blobs[entry.oid] = await repo.ask(repo.forge.refs.blob_evidence(entry.oid))
                entry = replace(entry, content=blobs[entry.oid])
            entries.append(entry)
        result.append(replace(commit, tree=tuple(entries)))
    return tuple(result)


async def _prepare(repo: RepoLanding, source_base: str, ids: tuple[str, ...], base: str) -> RewritePlan | None:
    commits = tuple([await _commit(repo, oid) for oid in (source_base, *ids, base)])
    paths: set[str] = set()
    seen: set[str] = set()
    base_entries = {entry.path: (entry.mode, entry.oid) for entry in commits[-1].tree}
    for before, after in pairwise(commits[:-1]):
        left = {entry.path: (entry.mode, entry.oid) for entry in before.tree}
        right = {entry.path: (entry.mode, entry.oid) for entry in after.tree}
        for path in left.keys() | right.keys():
            old, new, onto = left.get(path), right.get(path), base_entries.get(path)
            if old == new or path in seen:
                continue
            seen.add(path)
            if (
                old is not None
                and new is not None
                and onto is not None
                and old != onto
                and old[0] == new[0] == onto[0]
                and old[0] in {"100644", "100755"}
            ):
                paths.add(path)
    loaded = await _hydrate(repo, commits, paths)
    return prepare(loaded[0], loaded[1:-1], loaded[-1])


def _identity(repo: RepoLanding, number: int) -> dict[str, object]:
    return {
        "chunk": repo.run.visit.chunk.chunk_id,
        "node": repo.run.visit.node.node_id,
        "repo": repo.repo,
        "record": repo.record.record,
        "forge": str(repo.record.forge_id),
        "branch": repo.branch,
        "base_branch": repo.base_branch,
        "submission": repo.commit,
        "pr": number,
    }


async def _chain(repo: RepoLanding, base: str, head: str) -> tuple[str, ...] | None:
    comparison = await repo.compare(base, head)
    if comparison is None or comparison.commits_truncated or comparison.merge_base is None:
        return None
    by_id = {c.revision: c for c in comparison.commits}
    result: list[str] = []
    current = head
    while current != comparison.merge_base:
        c = by_id.get(current)
        if c is None or len(c.parents) != 1 or current in result:
            return None
        result.append(current)
        current = c.parents[0]
    return tuple(reversed(result))


async def record_intent(repo: RepoLanding, number: int, source: str, base: str) -> bool:
    """Prove replay support and record its immutable input before issuing a write."""
    comparison = await repo.compare(base, source)
    chain = await _chain(repo, base, source)
    if comparison is None or comparison.merge_base is None or not chain:
        return False
    source_base = comparison.merge_base
    if await _prepare(repo, source_base, chain, base) is None:
        return False
    data = {
        **_identity(repo, number),
        "source": source,
        "source_base": source_base,
        "base": base,
        "commits": list(chain),
    }
    content = json.dumps(data, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(content.encode()).hexdigest()
    await repo.run.post(f"rewrite-intent/{repo.repo}/{number}/{digest}", content)
    return True


async def admits(repo: RepoLanding, number: int, head: str) -> bool:
    """A rewrite must match a complete replay whose source is itself admitted."""
    markers = repo.run.visit.markers

    async def admitted(candidate: str, seen: frozenset[str]) -> bool:
        if candidate in seen:
            return False
        if (await gate_head(repo, candidate)).admitted():
            return True
        for marker in markers:
            if not marker.name.startswith(f"rewrite-intent/{repo.repo}/{number}/"):
                continue
            try:
                digest = hashlib.sha256(marker.data.encode()).hexdigest()
                if marker.name != f"rewrite-intent/{repo.repo}/{number}/{digest}":
                    continue
                loaded: object = json.loads(marker.data)
                if not isinstance(loaded, dict):
                    continue
                data = cast(dict[str, Any], loaded)
                if any(data.get(k) != v for k, v in _identity(repo, number).items()):
                    continue
                source, source_base, base, ids = data["source"], data["source_base"], data["base"], data["commits"]
                if not all(isinstance(x, str) for x in (source, source_base, base)) or not isinstance(ids, list):
                    continue
                values = cast(list[object], ids)
                if not values or not all(isinstance(x, str) for x in values) or values[-1] != source:
                    continue
                ids = cast(list[str], values)
                if not await admitted(source, seen | {candidate}):
                    continue
                plan = await _prepare(repo, source_base, tuple(ids), base)
                if plan is None:
                    continue
                rewritten: list[Commit] = []
                current = candidate
                for _ in ids:
                    commit = await _commit(repo, current)
                    rewritten.append(commit)
                    if len(commit.parents) != 1:
                        break
                    current = commit.parents[0]
                paths = {path for commit in plan.commits for path, entry in commit.tree if entry.oid is None}
                hydrated = await _hydrate(repo, tuple(reversed(rewritten)), paths)
                if proves(plan, hydrated):
                    return True
            except (KeyError, ValueError, TypeError):
                continue
        return False

    return await admitted(head, frozenset())

"""Durable, declaration-bound authorization of automatic rebase updates."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from blizzard.hub.delivery.actions.landing.head_gate import gate_head
from blizzard.hub.delivery.actions.landing.repo import RepoLanding
from blizzard.hub.delivery.actions.landing.rewrite import Commit, TreeEntry, prepare, proves


async def _commit(repo: RepoLanding, oid: str) -> Commit:
    refs = repo.forge.refs
    evidence = await repo.ask(refs.commit_evidence(oid))
    entries = await repo.ask(refs.tree_evidence(evidence.tree))
    tree: list[TreeEntry] = []
    for entry in entries:
        content = None if entry.mode == "160000" else await repo.ask(refs.blob_evidence(entry.revision))
        tree.append(TreeEntry(entry.path, entry.mode, entry.revision, content))
    author = json.dumps([evidence.author_name, evidence.author_email, evidence.author_date])
    return Commit(evidence.revision, evidence.parents, tuple(tree), author, evidence.message)


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
    source_commits = tuple([await _commit(repo, oid) for oid in chain])
    if prepare(await _commit(repo, source_base), source_commits, await _commit(repo, base)) is None:
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
                source_commits = tuple([await _commit(repo, oid) for oid in ids])
                plan = prepare(await _commit(repo, source_base), source_commits, await _commit(repo, base))
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
                if proves(plan, tuple(reversed(rewritten))):
                    return True
            except (KeyError, ValueError, TypeError):
                continue
        return False

    return await admitted(head, frozenset())

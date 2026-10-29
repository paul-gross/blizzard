"""Whether a live head is still the submitted work: the submitted commit itself, or a descendant of it through base
merges alone — the gate every landing policy puts a head through before it lands."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.hub.delivery.actions.landing.base_merge import contributes_only_base_change
from blizzard.hub.delivery.actions.landing.repo import RepoLanding
from blizzard.hub.forge.landing import CommitSummary, ComparisonStatus


@domain_model
@dataclass(frozen=True)
class HeadGate:
    """Whether a live change head is still the submitted work; ``offenders`` names what broke it."""

    offenders: tuple[str, ...]

    def admitted(self) -> bool:
        return not self.offenders


def first_parent_chain(
    commits: tuple[CommitSummary, ...], head: str, submitted: str
) -> tuple[list[CommitSummary], str | None]:
    """``head``'s first-parent commits back to ``submitted``, and why the walk failed, if it did."""
    by_revision = {c.revision: c for c in commits}
    chain: list[CommitSummary] = []
    current = head
    while current != submitted:
        commit = by_revision.get(current)
        if commit is None or not commit.parents:
            return chain, f"{current} is not reachable from {submitted} within the listed commits"
        chain.append(commit)
        if len(chain) > len(by_revision):
            return chain, f"{current} has no readable first parent"
        current = commit.parents[0]
    return chain, None


async def gate_head(repo: RepoLanding, head: str) -> HeadGate:
    """Whether ``head`` is the repository's submitted commit or descends from it through base merges alone. An
    answer it cannot prove refuses; a forge that did not answer raises ``ForgeUnavailable``."""
    submitted = repo.commit
    if head == submitted:
        return HeadGate(())
    ahead = await repo.compare(submitted, head)
    if ahead is None:
        return HeadGate((f"the forge would not compare {submitted}...{head}",))
    if ahead.status is ComparisonStatus.IDENTICAL:
        return HeadGate(())
    if ahead.status is not ComparisonStatus.AHEAD:
        return HeadGate((f"{head} does not descend from the submitted commit {submitted} (it was rewritten or reset)",))
    if ahead.commits_truncated:
        return HeadGate((f"the commit list between {submitted} and {head} is truncated",))
    chain, unreachable = first_parent_chain(ahead.commits, head, submitted)
    if unreachable is not None:
        return HeadGate((unreachable,))
    offenders: list[str] = []
    for commit in chain:
        sha = commit.revision
        if len(commit.parents) != 2:
            offenders.append(f"{sha} (not a merge of the base branch)")
            continue
        first, second = commit.parents
        if not await repo.base_holds(second):
            offenders.append(f"{sha} (merges {second}, which the base branch does not hold)")
            continue
        merged = await repo.compare(first, sha)
        base_side = await repo.compare(first, second)
        merge_base = base_side.merge_base if base_side is not None else None
        if merged is None or base_side is None or merge_base is None:
            offenders.append(f"{sha} (its content could not be compared with the base branch's change)")
            continue
        feature_side = await repo.compare(merge_base, first)
        if feature_side is None:
            offenders.append(f"{sha} (its content could not be compared with the base branch's change)")
        elif not contributes_only_base_change(merged, base_side, feature_side):
            offenders.append(f"{sha} (adds content beyond the base branch's change)")
    return HeadGate(tuple(offenders))

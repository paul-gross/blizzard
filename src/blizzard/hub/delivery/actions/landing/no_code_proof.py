"""The one strict proof that a repository needs no code landed, and the one way a policy records it.

No code is owed only on affirmative evidence: the base branch holds the submitted commit and the live branch, and the
live head is the submitted work or a descendant through base merges alone. A head that cannot be read, or a
comparison the forge refused, proves nothing."""

from __future__ import annotations

from blizzard.hub.delivery.actions.landing.head_gate import gate_head
from blizzard.hub.delivery.actions.landing.repo import NoCode, RepoLanding
from blizzard.hub.forge.landing import ForgeRejected


async def proves_no_code(repo: RepoLanding, head: str | None = None, *, gated: bool = False) -> bool:
    """Whether the base already holds the submission and the live branch, and the live head is admitted.

    ``head`` is a live head the caller already read; ``gated`` says the caller also put it through the head gate, so
    neither is repeated. A forge that did not answer raises ``ForgeUnavailable``."""
    if not await repo.base_holds(repo.commit) or not await repo.base_holds(repo.branch):
        return False
    if head is None:
        try:
            head = await repo.ask(repo.forge.refs.branch_tip(repo.branch))
        except ForgeRejected:
            return False
    if head is None:
        return False
    return gated or (await gate_head(repo, head)).admitted()


async def record_no_code(repo: RepoLanding) -> NoCode:
    """Write the repository's declaration-bound no-code evidence; it is not a landing."""
    repo.run.say(f"{repo.repo}:{repo.branch} adds no commit {repo.base_branch} does not already have — no code to land")
    name, content = repo.run.visit.no_code_marker(repo.repo)
    await repo.run.post(name, content)
    return NoCode()

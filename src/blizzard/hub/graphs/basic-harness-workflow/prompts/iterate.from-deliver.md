You are re-entering this node because `deliver` found every remaining CI failure inherited from the base branch: the
same check is already red on the base itself, and a one-time re-run did not turn it green. Read
`blizzard runner artifact get delivery-findings --node deliver --content` for the failing checks, the base branch, and
each one's re-run signature.

The branch is intact; nothing about the change itself is in question — only the repair is. Repair the base failure as
its own commit, separate from the chunk's feature commits, so the landing stays reviewable and revertable on its own;
do not fold it into an existing commit.

Another chunk blocked on the same base failure may repair it first — that race is expected, not a problem. If your
repair commit later rebases to empty at pre-push because the fix already landed, that is a success.

**Loop bound.** Read `blizzard runner chunk history` first. If `deliver` has already routed here on this exact
signature — the same repo, check, and head sha named in `delivery-findings`, with no `git_commit` artifact declared
since — repairing again will not help: escalate with `blizzard runner ask` instead of repeating the attempt.

# Pre-push rebase (advanced-development-workflow)

You are working a chunk's **pre-push** node-step — the integration step before delivery. Rebase the change onto the
current base branch, absorb whatever that costs, and triage how much the integration disturbed the validated work. You
carry the build context. What verification established is not in your session — read it from the report
(`blizzard runner artifact get verification-report --node verify --content`) before you triage against it.

## Start from what is actually there

You may be entering after review, or retrying after a crash or a delivery bounce. In each repo, fetch first — the
worktree's view of the base branch is as stale as the moment the environment was acquired — then check what the branch
carries and whether it is already on top of the current base. A repo already rebased and pushed needs no second rebase,
but it still needs its tip declared (below).

## What must be true when you finish

**One branch per repo.** Every environment's work for a repo is rolled up into a single branch, in one environment —
always, not only when several environments are in play. With one environment the roll-up is a free no-op; with several
it is the whole job, and making it conditional is how a chunk delivers a fraction of its work. Delivery refuses a repo
that arrives with two branches rather than picking one.

**Every ahead repo is rebased onto the current base.** For each repo ahead of its upstream: fetch, then rebase the
branch onto the latest base branch — `origin/master` unless the repo records another. Where a repo has work in more than
one environment, rebase those onto each other first so the repo ends with a single branch, then rebase that onto the
base. Resolve every conflict **inside the rebase** — never abandon it for a merge, never skip a commit. Keep each
resolution minimal and faithful to both sides' intent, and note every file a resolution touched. Conflicts between two
environments' work on the same repo are ordinary conflicts and yours to resolve here — you hold the build context the
conflicting hunks came out of, which is exactly why the roll-up belongs at this node and not in delivery.

**The procedural checks are green on the rebased result.** Run the project's linter and the unit tests covering what the
change — and any conflict resolution — touched. Scope the test run by judgement: targeted, not the entire suite; full
verification depth belongs to the verify node.

**Each rewritten branch is pushed** — with `--force-with-lease`, never a bare `--force`. The lease is what makes the
push refuse rather than clobber if anything else moved the branch meanwhile.

**Every repo you touched is declared, every time.** For each, run
`blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>` — `<repo>` is that repo's name in the
environment's repo manifest (never an `owner/name` slug, a path, or a URL), `<sha>` is the full commit sha, and
`--env <id>` is added if the chunk holds more than one environment. Delivery fast-forwards the base branch to the sha
you declare here, not to whatever the branch happens to point at: a rebase you push but never re-declare leaves delivery
aimed at the commit the rebase orphaned, which can never fast-forward and bounces back to this node indefinitely.
Declare every repo even when its rebase was a no-op — re-declaring an unchanged tip is harmless, while a silently
omitted repo is one delivery never learns about, and a chunk that reaches delivery with nothing declared fails outright
rather than reporting a landing.

## Submit

Submit the outcome as the node's `pre-push-summary` asset before you declare done: run
`blizzard runner artifact create --name pre-push-summary` with the content on stdin — per-repo rebase result, every
conflict and how it was resolved, what lint and tests ran and their results, and your severity triage: no conflicts
worth naming, mechanical-only resolutions, or resolutions that made semantic choices.

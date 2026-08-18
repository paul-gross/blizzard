# Pre-push rebase (advanced-development-workflow)

You are working a chunk's **pre-push** node-step — the integration step before delivery. Rebase the change onto the
current base branch, absorb whatever that costs, and triage how much the integration disturbed the validated work. What
verification established is not in your session — read it from the report
(`blizzard runner artifact get verification-report --node verify --content`) before you triage against it.

## Start from what is actually there

In each repo, fetch first — the worktree's view of the base branch is as stale as the moment the environment was
acquired — then check what the branch carries. A repo already rebased and pushed needs no second rebase, but it still
needs its tip declared (below).

## What must be true when you finish

**One branch per repo.** Every environment's work for a repo is rolled up into a single branch; delivery refuses a repo
that arrives with two.

**Every ahead repo is rebased onto the current base** — `origin/master` unless the repo records another. Where a repo
has work in more than one environment, rebase those onto each other first, then onto the base. Resolve every conflict
**inside the rebase** — never abandon it for a merge, never skip a commit — and note every file a resolution touched.

**The procedural checks are green on the rebased result.** Run the project's linter and the unit tests covering what the
change — and any conflict resolution — touched. Targeted, not the entire suite.

**Each rewritten branch is pushed** — with `--force-with-lease`, never a bare `--force`.

**Every repo you touched is declared, every time.** For each, run
`blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>` — `--env <id>` is added if the chunk
holds more than one environment. Delivery fast-forwards to the sha you declare, not to whatever the branch points at, so
declare every repo even when its rebase was a no-op.

## Submit

Submit the outcome as the node's `pre-push-summary` asset before you declare done: run
`blizzard runner artifact create --name pre-push-summary` with the content on stdin — per-repo rebase result, every
conflict and how it was resolved, what lint and tests ran, and your severity triage: no conflicts worth naming,
mechanical-only resolutions, or resolutions that made semantic choices.

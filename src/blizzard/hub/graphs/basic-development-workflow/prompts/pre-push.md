# Pre-push rebase

You are working a chunk's **pre-push** node-step — the integration step before delivery. Rebase the change onto the current base branch, absorb whatever that costs, and triage how much the integration disturbed the validated work. You carry the build context, which is what qualifies you to judge that.

## Start from what is actually there

You may be entering this node after review, or retrying after a crash or a delivery bounce. Do not assume where the branches stand. In each repo, fetch first — the worktree's view of the base branch is as stale as the moment the environment was acquired — then check what the branch carries and whether it is already on top of the current base. A repo already rebased and pushed needs no second rebase, but it still needs its tip declared.

## What must be true when you finish

**One branch per repo.** Every environment's work for a repo is rolled up into a single branch, in one environment — always, not only when several environments are in play. With one environment that roll-up is a no-op and costs you nothing; with several it is the whole job, and making it conditional is how a chunk ends up delivering a fraction of its work. Delivery refuses a repo that arrives with two branches rather than picking one, so an un-rolled-up change-set fails here rather than landing half of itself.

**Every ahead repo is rebased onto the current base.** For each repo ahead of its upstream: fetch, then rebase the branch onto the latest base branch — `origin/master` unless the repo records another. Where a repo has work in more than one environment, rebase those onto each other first so the repo ends with a single branch, then rebase that onto the base.

Resolve every conflict **inside the rebase** — never abandon it for a merge, never skip a commit. Keep each resolution minimal and faithful to both sides' intent, and note every file a resolution touched. Conflicts between two environments' work on the same repo are ordinary conflicts and are yours to resolve here; you hold the change's full context, which is exactly why the roll-up belongs at this node and not in delivery.

**The procedural checks are green on the rebased result.** Run the project's linter and the unit tests covering what the change — and any conflict resolution — touched. Scope the test run by judgement: targeted, not the entire suite. This lane has no separate verify node to fall back on for a fuller pass, so if a resolution reshapes behavior in a way targeted tests cannot settle, say so in your triage rather than trying to re-verify everything here.

**Each rewritten branch is pushed.** The rebase rewrote history, so push with `--force-with-lease`, never a bare `--force`. The lease is what makes the push refuse rather than clobber if anything else moved the branch meanwhile.

**Every repo you touched is declared, every time.** Run `blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>` for each.
- `<repo>` is that repo's name in the environment's repo manifest — not an `owner/name` slug, a path, or a URL.
- `<sha>` is the full commit sha, never abbreviated.
- Add `--env <id>` if the chunk holds more than one environment.

Delivery fast-forwards the base branch to the sha you declare here, not to whatever the branch happens to point at. A rebase you push but never re-declare leaves delivery aimed at the commit the rebase orphaned, which can never fast-forward and will bounce back to this node indefinitely. Declare every repo even when its rebase was a no-op: re-declaring an unchanged tip is harmless, while a repo you silently omit is a repo delivery never learns about — a chunk that reaches delivery with nothing declared fails outright rather than reporting a landing.

## Submit

Record the outcome as the node's `pre-push-summary` asset before you declare done: run `blizzard runner artifact create --name pre-push-summary` with the content on stdin — per-repo rebase result, every conflict and how it was resolved, what lint and tests ran and their results, and your severity triage: no conflicts worth naming, mechanical-only resolutions, or resolutions that made semantic choices.

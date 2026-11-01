# Resolve (advanced-development-workflow)

You are working a chunk's **resolve** node-step. The deliver node could not land the work; diagnose why and repair *only
that*. The change has already passed verify, review, and pre-push — preserve that validated state, do not redo it.

## Diagnose before you touch anything

Start with `blizzard runner artifact get delivery-findings --node deliver --content`; when it is absent, fall back to
the `hub-log.land` log, the bounce envelope, and the PRs and their check results on the forge. Check the state as it is
*now*, not as the bounce described it — a self-clearing state may have cleared, and an earlier attempt may already have
repaired a repo. Then establish which case you are in:

### 1. Merge conflict

The base branch advanced under the feature branch and the PR reads dirty. Fetch, then **merge the base branch into the
feature branch** — do not rebase, do not force-push, never touch the base branch from a node. Name the merge commit
explicitly (`Merge <base-branch> into <branch>`), resolve the conflicts, push, and track honestly whether any hunk
required a **semantic choice** or every conflict was mechanical.

### 2. Real defect

CI on the PR is red on a check the base branch does not also fail — `deliver` already excludes a check failing on both
head and base, re-running it once before ever reaching here, so anything routed to you is not inherited from the base.
When the failure looks intermittent rather than caused by the change, it is case 4. Otherwise do not fix it here:
capture exactly which check failed and why in the `resolve-report`; your findings route back to build.

### 3. Foreign commit on the PR head

`delivery-findings/foreign-head` (read it with `--node deliver`) says the PR head carries a commit beyond the one the
chunk submitted and its own base merges, and names it; `delivery-findings` may still show an earlier wait. Never select
`resolved` over it: the commit has not been verified, reviewed, or pre-pushed. Read what it changed. If it belongs to
the change, adopt it — fetch, declare the head as the repo's tip, and select `substantive` so it earns verification. If
it does not belong, select `broken`.

### 4. Flaky or intermittent CI check

A red check conclusion never clears on its own, so a failure you judge a flake is never answered `resolved` with no
change — investigate it. Read the failing job's log and check whether the same test fails elsewhere: the base branch's
recent runs, other PRs. To confirm, wait for the workflow run to conclude — `gh run watch <run-id>`, or poll
`gh run view <run-id>` until its status is `completed`; the forge refuses a re-run while it is in progress — then
`gh run rerun <run-id> --failed`. A re-run that passes means a flaky test or harness, a defect in the suite: name the
test, the failure, and its likely cause in the `resolve-report` and select `broken`, so build fixes the flake at its
root before the change delivers again. A re-run that fails the same way is the change's own defect — case 2.

### 5. Self-clearing state

The land pass itself raised, a mergeability state was still computing, or the forge state was momentary. Re-read it now.
Change nothing and say so — valid only once every repo's PR still open reads mergeable and no check on the head sha is
red.

## Declare every tip before you leave

If anything you did moved a repo's branch, push it and re-declare the new tip:
`blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>` — `--env <id>` is added if the chunk
holds more than one environment. Delivery opens its PR against the branch you declare, so declare even when you changed
nothing — an undeclared repo never reaches delivery at all.

Submit what you found, which case it was, and what you did per repo as the node's `resolve-report` asset before you
declare done: run `blizzard runner artifact create --name resolve-report` with the content on stdin.

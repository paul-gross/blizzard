# Retrospective

You work this prompt at a chunk's `retrospective` node-step, which closes the chunk after `deliver` reported that every
repo's PR merged. Re-derive that report rather than trusting it, then write the closing reflection.

Read the chunk's own record first — `blizzard runner chunk history` for its transitions and bounces, then its asset
trail: the work item(s) the chunk wraps, the review findings, and `pre-push`'s own diary —
`blizzard runner artifact get retrospective --node pre-push --content` (`--node` required: `pre-push` produces one
under this same name, so an unqualified read is ambiguous) and `blizzard runner artifact get pre-push-summary
--content`, the only session-local record of the integration leg, since that lineage never saw `build` or `iterate`.

## Verify the landing

Fetch each repo's own worktree before checking anything: its view of the base branch was last refreshed when the
environment was acquired.

`blizzard runner artifact list` returns one `git_commit` entry per repo per node that declared one; in this lane `build`,
`iterate`, and `pre-push` can each declare, since a rebase or a fresh fix re-pushes and re-declares. It names the branch
that was pushed, not the merged sha — read each PR's own state rather than assuming from it.

Per repo, check its PR's merge state from inside that repo's worktree so the query targets the right forge (on GitHub,
`gh pr view --json state,mergedAt`), then test the PR merge commit's reachability, not the sha the newest declaration
carries, with `git merge-base --is-ancestor <merged sha>
origin/<base>` — `origin/master` unless the repo records another; `gh pr view --json mergeCommit` gives the merged sha
— exit 0 means reachable; comparing branch tips or log output answers a different question.

Check whether the landing turned the base branch's own gate red separately, per repo, by querying the gate by the PR's
merge commit rather than by branch. A completed red run is a real finding: raise it here and leave it for the standing
cross-chunk analysis pass to route onward.

Confirm the chunk's originating work item is closed: `blizzard runner work-items <chunk-id>` gives each work ref's
`web_url`, and since the work item carries no closed/open field the forge is asked directly for the issue's `state`. A
forge-side "closes on merge" convention is opportunistic rather than guaranteed, so an open work item is recorded as a
finding and not read as evidence that the landing failed.

The outcome of these checks goes in the retrospective asset's Landing Verification section whatever it was, and a clean
landing is stated as having checked out clean, not merely as having landed.

This node never repairs a discrepancy it finds and never routes one backward: the finding is recorded and reported
plainly for a human to resolve, and the delivered work is not changed from here.

## Write the retrospective

The retrospective carries these sections:

- **Landing Verification**
- **What Went Well**
- **What Didn't Go Well**
- **Harness / Context Improvements** — concrete, actionable changes to the harness, tooling, agent docs, or conventions
  that would make the next run faster, more accurate, or more autonomous. Because this lane's work is itself the
  harness, that section says plainly whether the change just made achieved what it was meant to.
- **What We Skipped** — untested paths, deferred work, and known gaps.

The writing is honest and specific, naming files and findings rather than impressions.

## Close the node

You MUST run `blizzard runner artifact create --name retrospective` with the retrospective on stdin; the submission is
mandatory and is what closes the node. Once the asset is submitted, carry out whatever post-delivery work this
workspace's own agent context declares for the node, honoring any warning it gives about how that work behaves.

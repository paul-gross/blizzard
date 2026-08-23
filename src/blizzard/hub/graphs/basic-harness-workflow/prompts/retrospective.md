# Retrospective

You work this prompt at a chunk's `retrospective` node-step, which closes the chunk after `deliver` reported that every
repo's base branch fast-forwarded to this chunk's commit. Re-derive that report rather than trusting it, then write the
closing reflection.

Read the chunk's own record first — `blizzard runner chunk history` for its transitions and bounces, then its asset
trail: the work item(s) the chunk wraps and the review findings.

## Verify the landing

Fetch each repo's own worktree before checking anything: its view of the base branch was last refreshed when the
environment was acquired, and the fast-forward happened on the forge.

`blizzard runner artifact list` returns one `git_commit` entry per repo per node that declared one; in this lane every
declaration comes from `build`, including any re-declaration after a deliver-conflict bounce rebased and re-pushed.
Verify the newest `epoch` entry per repo; an older, superseded declaration is expected to be unreachable after a rebase
and is not a discrepancy.

Test reachability specifically with `git merge-base --is-ancestor <sha> origin/<base>` — `origin/master` unless the repo
records another — where exit 0 means reachable; comparing branch tips or reading log output answers a different
question.

Check whether the landing turned the base branch's own gate red separately, per repo, by querying the gate by the
fast-forwarded commit itself rather than by branch, since this lane fast-forwards the base ref directly and leaves no
separate merge commit. A completed red run is a real finding: raise it in this asset with its disposition and leave it
there for the standing cross-chunk analysis pass to route onward.

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

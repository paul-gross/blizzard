# Retrospective

Deliver reported every repo's PR merged; this node re-derives that report rather than trusting it, then writes the
closing reflection. This node never repairs or routes backward a discrepancy — record one plainly as a finding for a
human to resolve — and it does not change the delivered code; name files and findings, not vibes.

## Gather the record

Read the asset trail — the chunk's work item(s), the review findings, and the pre-push summary — and read
`blizzard runner chunk history`, which lists transitions and bounces oldest-first, including bounced attempts that
produced no artifact. This lane keeps no per-node retrospective diary to synthesize.

## Verify the landing

`blizzard runner artifact list` shows one `git_commit` entry per repo per node that declared one; it names the branch
pre-push pushed, not the merged sha — read each PR's own state rather than assuming from it.

Fetch in each repo's worktree before verifying — its base-branch view dates from environment acquisition. Per repo,
check its PR's merge state from inside that repo's worktree so the query targets the right forge (on GitHub,
`gh pr view --json state,mergedAt`), then test the PR merge commit's reachability, not the sha pre-push declared, with
`git merge-base --is-ancestor <merged sha> origin/<base>` (`origin/master` unless the repo records another;
`gh pr view --json mergeCommit` gives the merged sha) — that predicate specifically, not branch-tip comparison or log
reading.

Check whether the landing turned the base branch's gate red, querying by the PR's merge commit per repo — not by
branch. A completed red gate run is a real finding: raise it in the asset with its disposition and leave it there for
the routine cross-chunk analysis pass to route onward.

Verify the originating work item closed: `blizzard runner work-items <chunk-id>` gives each work ref's `web_url`; the
item has no open/closed field — ask the forge for the issue's `state`. Closes-on-merge is opportunistic, never
guaranteed — an open item is a finding to record, not evidence the landing failed.

## Write and submit the asset

The asset's sections are:

- **Landing Verification** — record the verification outcome whatever it is; a clean landing states that it checked out
  clean, not just that it landed.
- **What Went Well**
- **What Didn't Go Well**
- **Harness / Context Improvements** — concrete, actionable changes to harness, tooling, agent docs, or conventions
  making the next run faster, more accurate, or more autonomous. This section is the point of the retrospective.
- **What We Skipped** — untested paths, deferred work, known gaps.

Submit the reflection with `blizzard runner artifact create --name retrospective`, content on stdin. The asset goes
first so the recorded reflection survives whatever the post-delivery work costs.

## Post-delivery

After submitting the asset, do whatever post-delivery work the workspace's own agent context declares for this node —
redeploy, publish, notify — honoring its warnings; none declared leaves the node at the reflection.

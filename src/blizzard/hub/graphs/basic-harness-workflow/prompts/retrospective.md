# Retrospective (basic-harness-workflow)

You are working a chunk's **retrospective** node-step. Deliver *reported* that the work landed — every repo's base
branch fast-forwarded to this chunk's commit. This node re-derives that report rather than taking it on faith, then
writes the closing reflection.

## Start from what is actually there

Read `blizzard runner chunk history` — the chunk's transitions and bounces, oldest-first, including any bounced attempt
that produced no artifact. Then read its asset trail: the work item(s) the chunk wraps and the review findings. This is
a lightweight lane, so there is no per-node `retrospective` diary to synthesize.

## Verify the landing before writing anything

Fetch in each repo's own worktree first. The worktree's view of the base branch was last refreshed when the environment
was acquired and never since, while the fast-forward itself happened on the forge, not in this worktree.

Then, per repo:

1. **The landed sha is reachable from base.** `blizzard runner artifact list` returns one `git_commit` entry per repo
   per node that declared one. This lane has no `pre-push` node, so every declaration comes from `build` — including a
   re-declaration after a deliver-conflict bounce rebased and re-pushed it. Take the **newest `epoch`** entry per repo;
   an older, superseded declaration is expected to be unreachable after a rebase and is not a discrepancy. Test
   reachability with `git merge-base --is-ancestor <sha> origin/<base>` — `origin/master` unless the repo records
   another — where exit 0 means reachable. Use that predicate specifically; comparing branch tips or reading log output
   answers a different question.
2. **The chunk's originating work item is closed.** `blizzard runner work-items <chunk-id>` gives you each work ref's
   `web_url`. The work item carries no closed/open field, so ask the forge directly for the issue's `state`. A
   forge-side "closes on merge" convention is opportunistic, never guaranteed, so treat an open item as a finding to
   record, not as evidence the landing failed.

This lane fast-forwards a base ref directly rather than merging a PR, so there is no PR-merge leg to check.

Record the result in the retrospective asset's **Landing Verification** section whatever the outcome. A clean landing
states that it checked out clean, not just that it landed.

This lane has **no `resolve` node** — its deliver failure path already returns to `build`. So a discrepancy this check
finds is never something retrospective repairs or routes backward for itself. Record what you found as a finding and
report it plainly; a human resolves it from there.

Separately, check whether the landing turned the base branch's own gate red. Query the gate **by the fast-forwarded
commit itself**, per repo — not by branch — since there is no separate merge commit in this lane. A completed red run is
a real finding: raise it in this asset as its disposition and leave it there — the routine analysis pass that reads
retrospectives across chunks gathers what they raise and routes each one onward.

## Submit

Submit the retrospective as this node's `retrospective` asset: run
`blizzard runner artifact create --name retrospective` with the content on stdin, with these sections:

- **Landing Verification** — the checks above, on every landing, clean or not.
- **What Went Well**
- **What Didn't Go Well**
- **Harness / Context Improvements** — concrete, actionable changes to the harness, tooling, agent docs, or conventions
  that would make the next run faster, more accurate, or more autonomous. This section is the point of the
  retrospective, doubly so in this lane, where the work itself IS the harness: what you just changed and what you are
  recommending are the same kind of thing, so say plainly whether the change achieved what it was meant to.
- **What We Skipped** — untested paths, deferred work, known gaps.

Keep it honest and specific. Name files and findings, not vibes. Do not change the delivered work from this node.

## Post-delivery

**After the asset is submitted**, carry out whatever **post-delivery** work this workspace asks of the node. Read the
workspace's own agent context for a post-delivery convention. A workspace that declares one — redeploying the landed
build, publishing an artifact, notifying something downstream — expects this node to honor it. A workspace that declares
none leaves this node at the reflection above. The asset goes first so a reflection already recorded survives whatever
the post-delivery work costs. Follow the workspace's convention for the rest, including any warning it gives about how
that work behaves.

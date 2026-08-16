# Retrospective (advanced-development-workflow)

You are working a chunk's **retrospective** node-step. Deliver *reported* that the work landed — every repo merged into
its base branch. This node stands at the last trust boundary before the chunk closes, so it re-derives that report
rather than taking it on faith.

## Start from what is actually there

This may be your first visit or your second — a chunk that found a discrepancy goes out to `resolve` and comes back
here. Read `blizzard runner chunk history` to see which: it gives you the chunk's transitions, migrations, and bounces,
oldest-first, including any bounced attempt that produced no artifact. If a `retrospective` asset from an earlier visit
exists, read it — `blizzard runner artifact get retrospective --node retrospective --content`. The `--node` is required,
not optional: every node in this graph produces a `retrospective`, and the bare form exits non-zero naming the
candidates rather than picking one. The discrepancy that asset named is what you are checking has been repaired.

Then read the chunk's asset trail: each node's own `retrospective` asset — the per-node diary you are synthesizing —
plus the plan of record (`reviewed-plan`, the gate's published plan; the `plan` asset is the author's draft of it), the
plan-review and review findings, the verification report, and the pre-push summary.

## Verify the landing before writing anything

Start from `blizzard runner artifact get delivery-findings --node deliver --content` when one exists — the same
diagnosis input `resolve.md` reads — instead of re-deriving from nothing.

Fetch in each repo's own worktree first. The worktree's view of the base branch was last refreshed when the environment
was acquired and never since, while the merge itself happened on the forge, not in this worktree. Then check three
things:

1. **The landed sha is reachable from base.** `blizzard runner artifact list` returns one `git_commit` entry per repo
   per node that declared one — `build`, plus `pre-push` or `resolve` again if either rewrote the branch. Take the
   **newest `epoch`** entry per repo, the same "latest wins" rule delivery itself uses. An older, superseded declaration
   for that repo is expected to be unreachable after a rewrite and is not a discrepancy. Test reachability with
   `git merge-base --is-ancestor <sha> origin/<base>` in that repo's own worktree — `origin/master` unless the repo
   records another — where exit 0 means reachable. Use that predicate specifically; comparing branch tips or reading log
   output answers a different question.
2. **That repo's PR is merged** — read its merge state on the forge (`gh pr view --json state,mergedAt`), from inside
   that repo's worktree so the query targets the right forge.
3. **The chunk's originating work item is closed.** `blizzard runner work-items <chunk-id>` gives you each work ref's
   `web_url`. The work item carries no closed/open field, so ask the forge directly for the issue's `state`. A
   forge-side "closes on merge" convention is opportunistic, never guaranteed — some sources honor no such convention at
   all — so an open item here is recorded as a finding. It is never on its own a reason to select `delivery-incomplete`;
   only legs 1 and 2 are.

Record the result in the retrospective asset's **Landing Verification** section whatever the outcome. A clean landing
states that it checked out clean, not just that it landed.

If leg 1 or leg 2 fails for any repo — that repo's newest declared sha not reachable from base, or its PR unmerged —
submit the retrospective asset now with the **Landing Verification** section naming the specific discrepancy. The
remaining sections can wait; they belong to the visit that actually closes the chunk. Your judgement then takes
`delivery-incomplete` instead of `recorded`.

Separately, check whether the landing turned the base branch's own gate red. Query the gate **by the PR's merge
commit**, per repo — not by branch, which would answer a different question. The merge commit is not in your envelope:
read it off the PR itself (`gh pr view --json mergeCommit`), since nothing else you hold identifies it. This is never a
reason to route backward — the code is on base and stays there — but a completed red run is a real finding. Record it
and file a follow-up issue for it as its disposition, using the filing convention [../docket.md](../docket.md) owns.

## Fold the findings docket

Before you write the closing reflection, fold the chunk's findings docket per [../docket.md](../docket.md). Enumerate
every id in the **newest** `plan-findings` asset and the **newest** `review-findings` asset — either may be an empty
list, since a clean gate or a clean review passes with nothing to record. Match each against the disposition records
across the chunk's node `retrospective` assets, then close every unmatched `should-fix` id by its target:

- An unmatched id whose target is a real repo file, describing a defect still present in the change, is open. File a
  forge issue for it — following this workspace's own issue-filing convention if it declares one, otherwise filing from
  inside that finding's own repo worktree, which its anchor names, so the issue lands on the right forge — and record
  that filing as its disposition.
- An unmatched id whose target is an **immutable artifact** — in practice a plan-apparatus finding against the consumed
  plan asset — has no repo target a fix could land on. Close it `accepted-wont-fix` with a stated reason, and do not
  file it.

`plan-findings` ids are in scope here like any other: the outcome is keyed on the finding's target, not on which node
produced it. An id with severity `folded` is closed by construction — the gate already fixed it in the `reviewed-plan`
it published; carry it into the fold table as `folded`, never open, never filed. An unmatched `blocking` id should not
occur; if one does, file it like the repo-targeted case and flag the anomaly.

## Submit

Submit the retrospective as this node's `retrospective` asset before you declare done, on **every** visit including a
discrepancy one: run `blizzard runner artifact create --name retrospective` with the content on stdin.

On a clean landing — or once a chunk returns here with a prior discrepancy repaired — write the full asset:

- **Landing Verification** — the three-leg check above and the merge-commit gate check.
- **What Went Well**
- **What Didn't Go Well**
- **Harness / Context Improvements** — concrete, actionable changes to the harness, tooling, agent docs, or conventions
  that would make the next run faster, more accurate, or more autonomous. This section is the point of the
  retrospective.
- **What We Skipped** — untested paths, deferred work, known gaps.
- **Findings Docket** — the fold table (id, source, severity, anchor, disposition, reference), or a note that the chunk
  had nothing to fold.

On a discrepancy visit — where your judgement selects `delivery-incomplete` — the asset needs only **Landing
Verification**, naming the specific discrepancy.

Keep it honest and specific. Name files and findings, not vibes. Do not change the delivered code from this node.

## Post-delivery

**Only on the visit that writes the full closing reflection** — never on a discrepancy visit, since the landing is not
actually complete yet and post-delivery work assumes it is — and **after the asset is submitted**, carry out whatever
**post-delivery** work this workspace asks of the node.

Read the workspace's own agent context for a post-delivery convention. A workspace that declares one — redeploying the
landed build, publishing an artifact, notifying something downstream — expects this node to honor it. A workspace that
declares none leaves this node at the reflection above. The asset goes first so a reflection already recorded survives
whatever the post-delivery work costs. Follow the workspace's convention for the rest, including any warning it gives
about how that work behaves.

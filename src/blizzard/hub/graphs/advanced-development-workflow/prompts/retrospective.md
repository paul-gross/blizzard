# Retrospective (advanced-development-workflow)

You are working a chunk's **retrospective** node-step. Deliver *reported* that the work landed; this node re-derives
that report rather than taking it on faith.

## Start from what is actually there

A chunk that found a discrepancy goes out to `resolve` and comes back — `blizzard runner chunk history` says which visit
this is. Read any earlier visit's asset — `blizzard runner artifact get retrospective --node retrospective --content`
(`--node` required: every node in this graph produces one) — then the chunk's asset trail: each node's `retrospective`,
the `reviewed-plan`, the plan-review and review findings, the verification report, and the pre-push summary.

## Verify the landing before writing anything

Start from `blizzard runner artifact get delivery-findings --node deliver --content` when one exists. Fetch in each
repo's worktree first, then check three things:

1. **The landed sha is reachable from base** — per repo, newest-`epoch` `git_commit` entry:
   `git merge-base --is-ancestor <sha> origin/<base>` exits 0.
2. **That repo's PR is merged** — read its merge state from inside that repo's worktree so the query targets the right
   forge (on GitHub, `gh pr view --json state,mergedAt`).
3. **The chunk's originating work item is closed** — `blizzard runner work-items <chunk-id>` gives each ref's `web_url`;
   ask the forge. An open item is a finding — never on its own a reason to select `delivery-incomplete`; only legs 1 and
   2 are.

Record the result in the asset's **Landing Verification** section either way. If leg 1 or 2 fails for any repo, submit
the asset now naming the discrepancy; your judgement then takes `delivery-incomplete`.

Separately, check whether the landing turned the base branch's own gate red — query by the PR's merge commit, per repo
(on GitHub, `gh pr view --json mergeCommit`). Never a reason to route backward: raise a red run as a finding in this
asset and leave it there for the cross-chunk analysis pass to gather.

## Fold the findings docket

Enumerate every id in the **newest** `plan-findings` and `review-findings` assets — an earlier round is superseded and
out of the fold; when a chunk had one, name it in the fold table. For each id:

- `folded` — closed by construction; the gate already fixed it.
- `should-fix` anchored at a real repo file — check the change as it stands. Fixed, or its refutation accepted: closed;
  say which. Still present: open — raise it with its anchor and what is left to do.
- `should-fix` on an immutable artifact — the plan-apparatus case — closed with a stated reason, never raised forward.
- `blocking` — resolved by the bounce that cleared it; one found still open is raised like the open should-fix case,
  marked still blocking.

## Submit

Submit this node's `retrospective` asset before you declare done, on **every** visit —
`blizzard runner artifact create --name retrospective`, content on stdin. On a clean landing, or once a prior
discrepancy is repaired, write the full asset:

- **Landing Verification** — the three-leg check and the merge-commit gate check.
- **What Went Well**
- **What Didn't Go Well**
- **Harness / Context Improvements** — concrete changes to make the next run faster, more accurate, or more autonomous —
  the point of the retrospective.
- **What We Skipped** — untested paths, deferred work, known gaps.
- **Findings Docket** — the fold table (id, source, severity, anchor, outcome), or a note that nothing needed folding.

On a discrepancy visit the asset needs only **Landing Verification**. Keep it honest and specific: name files and
findings, not vibes. Do not change the delivered code from this node.

## Post-delivery

Only on the visit that writes the full closing reflection, after the asset is submitted: carry out whatever
post-delivery work the workspace's own agent context asks of this node. Declaring none leaves this node at the
reflection above.

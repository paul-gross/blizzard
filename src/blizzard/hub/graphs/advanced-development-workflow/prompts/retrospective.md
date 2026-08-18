# Retrospective (advanced-development-workflow)

You are working a chunk's **retrospective** node-step. Deliver *reported* that the work landed; this node re-derives
that report rather than taking it on faith.

## Start from what is actually there

This may be your first visit or your second — a chunk that found a discrepancy goes out to `resolve` and comes back.
Read `blizzard runner chunk history` to see which. If a `retrospective` asset from an earlier visit exists, read it —
`blizzard runner artifact get retrospective --node retrospective --content` (the `--node` is required: every node in
this graph produces a `retrospective`). Then read the chunk's asset trail: each node's `retrospective`, the
`reviewed-plan`, the plan-review and review findings, the verification report, and the pre-push summary.

## Verify the landing before writing anything

Start from `blizzard runner artifact get delivery-findings --node deliver --content` when one exists. Fetch in each
repo's own worktree first, then check three things:

1. **The landed sha is reachable from base.** Take the newest-`epoch` `git_commit` entry per repo and test
   `git merge-base --is-ancestor <sha> origin/<base>` in that repo's worktree — exit 0 means reachable.
2. **That repo's PR is merged** — read its merge state on the forge, from inside that repo's worktree so the query
   targets the right forge (on GitHub, `gh pr view --json state,mergedAt`).
3. **The chunk's originating work item is closed.** `blizzard runner work-items <chunk-id>` gives each work ref's
   `web_url`; ask the forge for the issue's state. An open item is recorded as a finding — never on its own a reason to
   select `delivery-incomplete`; only legs 1 and 2 are.

Record the result in the asset's **Landing Verification** section whatever the outcome. If leg 1 or 2 fails for any
repo, submit the asset now naming the specific discrepancy; your judgement then takes `delivery-incomplete`.

Separately, check whether the landing turned the base branch's own gate red — query by the PR's merge commit, per repo
(on GitHub, `gh pr view --json mergeCommit`). Never a reason to route backward: record a red run as a finding and file a
follow-up issue for it.

## Fold the findings docket

Enumerate every id in the **newest** `plan-findings` and the **newest** `review-findings` asset — an earlier round is
superseded and out of the fold entirely; when a chunk had one, the fold table names it. For each id:

- A `folded` id is closed by construction — the gate already fixed it.
- A `should-fix` id anchored at a real repo file: check the change as it stands. Fixed, or its refutation accepted —
  closed; say which. Still present — open: file an issue on that repo's forge — the workspace's own issue-filing
  convention if it declares one, otherwise the forge's own tooling from inside that repo's worktree (on GitHub,
  `gh issue create`) — and record the URL.
- A `should-fix` id whose target is an immutable artifact — the plan-apparatus case — is closed with a stated reason,
  not filed.
- A `blocking` id was resolved by the bounce that cleared it; one found still open is filed like the open should-fix
  case, with the fold table saying it was found still blocking.

## Submit

Submit the retrospective as this node's `retrospective` asset before you declare done, on **every** visit: run
`blizzard runner artifact create --name retrospective` with the content on stdin. On a clean landing — or once a prior
discrepancy is repaired — write the full asset:

- **Landing Verification** — the three-leg check and the merge-commit gate check.
- **What Went Well**
- **What Didn't Go Well**
- **Harness / Context Improvements** — concrete changes that would make the next run faster, more accurate, or more
  autonomous. This section is the point of the retrospective.
- **What We Skipped** — untested paths, deferred work, known gaps.
- **Findings Docket** — the fold table (id, source, severity, anchor, outcome, reference), or a note that nothing needed
  folding.

On a discrepancy visit the asset needs only **Landing Verification**. Keep it honest and specific: name files and
findings, not vibes. Do not change the delivered code from this node.

## Post-delivery

Only on the visit that writes the full closing reflection, and after the asset is submitted, carry out whatever
post-delivery work this workspace asks of the node — read the workspace's own agent context for a post-delivery
convention. A workspace that declares none leaves this node at the reflection above.

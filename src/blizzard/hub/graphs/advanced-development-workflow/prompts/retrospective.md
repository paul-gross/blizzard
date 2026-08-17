# Retrospective (advanced-development-workflow)

You are working a chunk's **retrospective** node-step. Deliver *reported* that the work landed — every repo merged into
its base branch. This node stands at the last trust boundary before the chunk closes, so it re-derives that report
rather than taking it on faith.

## Start from what is actually there

This may be your first visit or your second — a chunk that found a discrepancy goes out to `resolve` and comes back.
Read `blizzard runner chunk history` to see which: it gives the chunk's transitions, migrations, and bounces,
oldest-first, including any bounced attempt that produced no artifact. If a `retrospective` asset from an earlier visit
exists, read it — `blizzard runner artifact get retrospective --node retrospective --content`. The `--node` is required:
every node in this graph produces a `retrospective`, and the bare form exits non-zero naming the candidates rather than
picking one. The discrepancy that asset named is what you are checking has been repaired.

Then read the chunk's asset trail: each node's own `retrospective` asset — the per-node diary you are synthesizing —
plus the plan of record (`reviewed-plan`; the `plan` asset is the author's draft of it), the plan-review and review
findings, the verification report, and the pre-push summary.

## Verify the landing before writing anything

Start from `blizzard runner artifact get delivery-findings --node deliver --content` when one exists, instead of
re-deriving from nothing. Fetch in each repo's own worktree first — its view of the base branch was last refreshed when
the environment was acquired, while the merge happened on the forge. Then check three things:

1. **The landed sha is reachable from base.** `blizzard runner artifact list` returns one `git_commit` entry per repo
   per node that declared one — `build`, plus `pre-push` or `resolve` again if either rewrote the branch. Take the
   **newest `epoch`** entry per repo, the same "latest wins" rule delivery uses; an older, superseded declaration is
   expected to be unreachable after a rewrite and is not a discrepancy. Test reachability with
   `git merge-base --is-ancestor <sha> origin/<base>` in that repo's own worktree — `origin/master` unless the repo
   records another — where exit 0 means reachable. Use that predicate specifically; comparing branch tips or reading log
   output answers a different question.
2. **That repo's PR is merged** — read its merge state on the forge (`gh pr view --json state,mergedAt`), from inside
   that repo's worktree so the query targets the right forge.
3. **The chunk's originating work item is closed.** `blizzard runner work-items <chunk-id>` gives each work ref's
   `web_url`; the work item carries no closed/open field, so ask the forge for the issue's `state`. A forge-side "closes
   on merge" convention is opportunistic, never guaranteed, so an open item is recorded as a finding — but it is never
   on its own a reason to select `delivery-incomplete`; only legs 1 and 2 are.

Record the result in the retrospective asset's **Landing Verification** section whatever the outcome — a clean landing
states that it checked out clean, not just that it landed.

If leg 1 or leg 2 fails for any repo, submit the retrospective asset now with **Landing Verification** naming the
specific discrepancy; the remaining sections belong to the visit that actually closes the chunk. Your judgement then
takes `delivery-incomplete` instead of `recorded`.

Separately, check whether the landing turned the base branch's own gate red. Query the gate **by the PR's merge
commit**, per repo — not by branch, which answers a different question. The merge commit is not in your envelope: read
it off the PR itself (`gh pr view --json mergeCommit`). This is never a reason to route backward — the code is on base
and stays there — but a completed red run is a real finding: record it and file a follow-up issue for it as its
disposition, using the same filing convention as the fold below.

## Fold the findings docket

Before the closing reflection, fold the chunk's findings. What follows restates the docket's own fold rules; read the
full docket with `blizzard runner artifact get docket --scope graph --content` if anything below is ambiguous, and fold
on the rules as stated here if that command fails — any error, rather than the docket's text — without retrying.
Enumerate every id in the **newest** `plan-findings` asset and the **newest** `review-findings` asset — either may be an
empty list. An earlier round is superseded and out of the fold entirely, its undisposed findings abandoned by design:
the review is a full cold pass over the change as it stands, so a defect still present is re-reported under a new id in
the newest asset, where the fold sees it. When a round was superseded, name that in the fold table. Match each id
against the disposition records across the chunk's node `retrospective` assets, then:

- A matched id is closed — carry its disposition into the fold table.
- An unmatched `should-fix` id whose target is a real repo file, describing a defect still present in the change, is
  open. File a forge issue for it — following this workspace's own issue-filing convention if it declares one (skill,
  format, label set), otherwise a plain `gh issue create` run from inside that finding's own repo worktree, which its
  anchor names, so the issue lands on the right forge — and record the filing as its disposition (`filed-as-issue`, with
  the URL). The filing *is* the disposition.
- An unmatched `should-fix` id whose target is an **immutable artifact** — in practice a plan-apparatus finding against
  the consumed plan asset — has no repo target a fix could land on. Close it `accepted-wont-fix` with a stated reason;
  do not file it. The outcome is keyed on the finding's target, not on which node produced it, so a `plan-findings` id
  anchored at a real repo file files exactly like the bullet above.
- An id with severity `folded` is closed by construction — the gate already fixed it in the `reviewed-plan` it
  published. Carry it into the fold table as `folded`; it is never open and never filed.
- An unmatched `blocking` id should not occur — one does not survive into the newest asset without a bounce that
  resolved it. If one somehow does, file it like the open should-fix case and say in the fold table that it was found
  still blocking.

A disposition citing an id absent from the newest asset of its kind is stale — the round it answered was superseded.
Ignore it.

## Submit

Submit the retrospective as this node's `retrospective` asset before you declare done, on **every** visit including a
discrepancy one: run `blizzard runner artifact create --name retrospective` with the content on stdin.

On a clean landing — or once a prior discrepancy is repaired — write the full asset:

- **Landing Verification** — the three-leg check and the merge-commit gate check.
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

Keep it honest and specific: name files and findings, not vibes. Do not change the delivered code from this node.

## Post-delivery

**Only on the visit that writes the full closing reflection** — never on a discrepancy visit, since post-delivery work
assumes a complete landing — and **after the asset is submitted**, carry out whatever **post-delivery** work this
workspace asks of the node. Read the workspace's own agent context for a post-delivery convention: a workspace that
declares one — redeploying the landed build, publishing an artifact, notifying something downstream — expects this node
to honor it, including any warning it gives about how that work behaves; a workspace that declares none leaves this node
at the reflection above. The asset goes first so a reflection already recorded survives whatever the post-delivery work
costs.

# Retrospective (advanced-development-workflow)

You are working a chunk's **retrospective** node-step. Your input is the chunk's own workflow and artifacts only: read
them, reflect, and fold the findings docket.

## Start from what is actually there

Read `blizzard runner chunk history` for the chunk's transitions and bounces, then its asset trail: each node's
`retrospective` diary (`blizzard runner artifact get retrospective --node <node> --content` — `--node` required, every
node in this graph produces one), the `reviewed-plan`, the plan-review and review findings, the verification report, and
the pre-push summary.

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

Submit this node's `retrospective` asset before you declare done —
`blizzard runner artifact create --name retrospective`, content on stdin:

- **What Went Well**
- **What Didn't Go Well**
- **Harness / Context Improvements** — concrete changes to make the next run faster, more accurate, or more autonomous —
  the point of the retrospective.
- **What We Skipped** — untested paths, deferred work, known gaps.
- **Findings Docket** — the fold table (id, source, severity, anchor, outcome), or a note that nothing needed folding.

Keep it honest and specific: name files and findings, not vibes. Do not change the delivered code from this node.

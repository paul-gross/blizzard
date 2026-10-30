# Retrospective

You work this prompt at a chunk's `retrospective` node-step, which closes the chunk. Your input is the chunk's own
workflow and artifacts only: read them, then write the closing reflection.

Read the chunk's own record first — `blizzard runner chunk history` for its transitions and bounces, then its asset
trail: the work item(s) the chunk wraps, the review findings, and `pre-push`'s own diary —
`blizzard runner artifact get retrospective --node pre-push --content` (`--node` required: `pre-push` produces one under
this same name, so an unqualified read is ambiguous) and `blizzard runner artifact get pre-push-summary --content`, the
only session-local record of the integration leg, since that lineage never saw `build` or `iterate`.

## Write the retrospective

The retrospective carries these sections:

- **What Went Well**
- **What Didn't Go Well**
- **Harness / Context Improvements** — concrete, actionable changes to the harness, tooling, agent docs, or conventions
  that would make the next run faster, more accurate, or more autonomous. Because this lane's work is itself the
  harness, that section says plainly whether the change just made achieved what it was meant to.
- **What We Skipped** — untested paths, deferred work, and known gaps.

The writing is honest and specific, naming files and findings rather than impressions. This node does not change the
delivered code.

## Close the node

You MUST run `blizzard runner artifact create --name retrospective` with the retrospective on stdin; the submission is
mandatory and is what closes the node.

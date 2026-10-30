# Retrospective

Your input is the chunk's own workflow and artifacts only: read them, then write the closing reflection. This node does
not change the delivered code; name files and findings, not vibes.

## Gather the record

Read the asset trail — the chunk's work item(s), the review findings, and the pre-push summary — and read
`blizzard runner chunk history`, which lists transitions and bounces oldest-first, including bounced attempts that
produced no artifact. This lane keeps no per-node retrospective diary to synthesize.

## Write and submit the asset

The asset's sections are:

- **What Went Well**
- **What Didn't Go Well**
- **Harness / Context Improvements** — concrete, actionable changes to harness, tooling, agent docs, or conventions
  making the next run faster, more accurate, or more autonomous. This section is the point of the retrospective.
- **What We Skipped** — untested paths, deferred work, known gaps.

Submit the reflection with `blizzard runner artifact create --name retrospective`, content on stdin.

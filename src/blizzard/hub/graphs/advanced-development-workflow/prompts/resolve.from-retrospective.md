# Resolve — re-entry after retrospective found an incomplete landing

You are re-entering the **resolve** node because retrospective re-derived deliver's landing report and found it
incomplete. This is not a pre-merge blocker — the situation `resolve.md` is written for — but a **partial** landing:
some repos are genuinely merged, others are not, in whatever mix the discrepancy left.

Read the specific discrepancy before doing anything:
`blizzard runner artifact get retrospective --node retrospective --content`. Then check each repo's current state
yourself — a repo may have merged since retrospective looked.

Repair only what is actually wrong, per repo:

- A repo whose delivery marker exists and whose ref is on base needs nothing. Leave it.
- A repo whose PR never merged: merge it, or diagnose and fix whatever blocked it, using the same triage `resolve.md`
  describes.
- A repo whose marker exists but whose ref never advanced — the marker recorded a landing that did not stick — is not
  something to hand-repair by pushing to the base branch yourself. `resolve.md`'s rule holds here too: never touch the
  base branch from a node. Escalate instead with `blizzard runner ask`, naming the repo and exactly what retrospective
  found versus what the base branch actually shows, so a human decides how to reconcile it.

Once every repo needing a merge is genuinely merged, select `resolved`. Deliver re-enters and reports `landed` outright:
every repo's marker now exists and its PR is genuinely merged, and the land script skips a repo whose marker is already
set. The repair you made here is what makes that re-entry a true no-op — it is not something deliver does for you.

Submit your `resolve-report` the same way `resolve.md` describes.

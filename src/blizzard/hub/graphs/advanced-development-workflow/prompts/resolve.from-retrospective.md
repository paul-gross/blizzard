# Resolve — re-entry after retrospective found an incomplete landing

You are re-entering the **resolve** node because retrospective re-derived deliver's landing report and found it
incomplete. This is not a pre-merge blocker — the situation the resolve node's standing instructions above are written
for — but a **partial** landing: some repos are genuinely merged, others are not.

Read the specific discrepancy first: `blizzard runner artifact get retrospective --node retrospective --content`. Then
check each repo's current state yourself — a repo may have merged since retrospective looked.

Repair only what is actually wrong, per repo:

- A repo whose delivery marker exists and whose ref is on base needs nothing. Leave it.
- A repo whose PR never merged: merge it, or diagnose and fix whatever blocked it, using the resolve node's own triage
  described above.
- A repo whose marker exists but whose ref never advanced — a recorded landing that did not stick — is not something to
  hand-repair by pushing to the base branch yourself; never touch the base branch from a node. Escalate with
  `blizzard runner ask`, naming the repo and exactly what retrospective found versus what the base branch shows, so a
  human decides how to reconcile it.

Once every repo needing a merge is genuinely merged, select `resolved`. Deliver re-enters and reports `landed` outright:
the land script skips a repo whose marker is already set, so the repair you make here is what makes that re-entry a true
no-op — it is not something deliver does for you.

Submit your `resolve-report` as described above.

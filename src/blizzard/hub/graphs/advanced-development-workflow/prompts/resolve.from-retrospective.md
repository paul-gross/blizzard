# Resolve — re-entry after retrospective found an incomplete landing

You are re-entering the **resolve** node because retrospective re-derived deliver's landing report and found it incomplete — not a pre-merge blocker, the situation `resolve.md` is written for, but a **partial** landing: some repos are genuinely merged, others are not, in whatever mix the discrepancy left. Read the specific discrepancy retrospective found before doing anything: `blizzard runner artifact get retrospective --node retrospective --content`.

Repair only what is actually wrong, per repo:

- A repo whose marker already exists and whose ref is on base needs nothing — leave it.
- A repo whose PR never merged: merge it (or diagnose and fix whatever blocked it, the same triage `resolve.md`'s own cases describe).
- A repo whose marker exists but whose ref never advanced (the marker recorded a landing that, for whatever reason, did not stick) is not something to hand-repair by pushing to the base branch yourself — `resolve.md`'s own rule holds here too: never touch the base branch from a node. Escalate instead: `blizzard runner ask`, naming the repo and exactly what retrospective found versus what the base branch actually shows, so a human decides how to reconcile it.

Once every repo needing a merge is genuinely merged, select `resolved` — deliver re-enters and reports `landed` outright, since every repo's marker now exists and its PR is genuinely merged (`land_pr_ci` skips a repo whose marker is already set and reports `landed` once nothing is pending, `land_pr_ci.py:287-289`); the repair you made here is what makes that re-entry a true no-op, not something deliver itself does. Submit your `resolve-report` the same way `resolve.md` describes.

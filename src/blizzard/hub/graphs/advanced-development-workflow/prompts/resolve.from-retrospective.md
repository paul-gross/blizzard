# Resolve — re-entry after retrospective found an incomplete landing

You are re-entering the **resolve** node because retrospective re-derived deliver's landing report and found it incomplete — not a pre-merge blocker, the situation `resolve.md` is written for, but a **partial** landing: some repos are genuinely merged, others are not, in whatever mix the discrepancy left. The `retrospective` asset carries the specific discrepancy retrospective found (a declared sha not reachable from base, or a repo's PR unmerged) — read it before doing anything.

Repair only what is actually wrong, per repo:

- A repo whose marker already exists and whose ref is on base needs nothing — leave it.
- A repo whose PR never merged: merge it (or diagnose and fix whatever blocked it, the same triage `resolve.md`'s own cases describe).
- A repo whose marker exists but whose ref never advanced (the marker recorded a landing that, for whatever reason, did not stick) is a hand-repair: bring that repo's base branch to the declared commit yourself.

Once every repo is genuinely merged, select `resolved` — deliver re-enters as a **clean no-op** for every repo already correctly landed (idempotence: a repo whose marker exists and whose PR is already merged is a no-op re-merge; `land_pr_ci` reports `landed` outright once nothing is pending) and completes whatever repair you made here for the rest. Submit your `resolve-report` the same way `resolve.md` describes.

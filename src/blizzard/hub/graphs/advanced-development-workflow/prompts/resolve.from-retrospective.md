# Resolve — re-entry after retrospective found an incomplete landing

You are re-entering the **resolve** node because retrospective re-derived deliver's landing report and found it
incomplete — a **partial** landing: some repos are genuinely merged, others are not.

Read the discrepancy first: `blizzard runner artifact get retrospective --node retrospective --content`. Then check each
repo's current state yourself — a repo may have merged since retrospective looked. Repair only what is actually wrong:

- A repo whose delivery marker exists and whose ref is on base needs nothing. Leave it.
- A repo whose PR never merged: merge it, or diagnose and fix what blocked it, using the triage above.
- A repo whose marker exists but whose ref never advanced: never repair this by pushing to the base branch yourself —
  escalate with `blizzard runner ask`, naming the repo and the discrepancy.

Once every repo needing a merge is genuinely merged, select `resolved`. Submit your `resolve-report` as described above.

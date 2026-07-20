# Build — re-entry after a delivery conflict

You are re-entering the **build** node because the deliver node could not land the work: a repo's branch no longer merges cleanly into the base — `master` advanced under it. You are still on your `feat/<slug>` feature branch with your commits intact; nothing has landed.

Bring the branch up to date and re-push, once per repo that conflicts:

- `git fetch origin`, then **merge `origin/master` into your `feat/<slug>` branch** and resolve the conflicts. A merge here is intended — it keeps the true history of what was integrated. Do **not** rebase, and do **not** the reverse (merging your branch into master) — you never touch `master` from a node.
- Give the merge an explicit commit message naming the branch — `Merge master into feat/<slug>` — never git's default `Merge remote-tracking branch …` text.
- Push the branch again once the merge is resolved.

Deliver runs again once you exit; the reported facts move the chunk.

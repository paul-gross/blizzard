# Build — re-entry after a delivery conflict

You are re-entering the **build** node because the deliver node could not land the work: a repo's branch no longer
merges cleanly into the base — the base advanced under it. Your commits are intact on the `feat/<slug>` feature branch;
nothing has landed in the conflicting repos.

Check each repo before you touch it. Some repos may already have landed — a chunk spanning several repos advances them
one at a time. A repo whose base already contains this chunk's commit is done; leave it alone.

For each repo that actually conflicts:

- Fetch, then **merge the base branch into your `feat/<slug>` branch** and resolve the conflicts. A merge is intended
  here — it keeps the true history of what was integrated. Do **not** rebase, and do not merge in the other direction:
  you never touch the base branch from a node.
- Give the merge an explicit commit message naming the branch — `Merge master into feat/<slug>` — never git's default
  `Merge remote-tracking branch …` text.
- Push the branch once the merge is resolved.
- Re-declare the tip: `blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>`, with the full
  sha.

Deliver runs again once you exit; the facts you declare are what move the chunk.

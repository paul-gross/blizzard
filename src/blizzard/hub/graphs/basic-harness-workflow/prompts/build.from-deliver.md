# Build — re-entry after deliver could not land cleanly

You are re-entering the **build** node because `deliver` did not land cleanly. Two distinct causes route here:

- **conflict** — a repo did not fast-forward: the base moved after this chunk's commit was made, so the update was no longer a fast-forward and the forge rejected it. Nothing landed in the rejected repos.
- **failure** — the land script itself failed or crashed (a missing environment variable, an unauthorized or failed delivery-marker write) instead of reporting a clean outcome.

Either way, this is not a verdict on the work itself — the change already passed build and review. This lane has no `pre-push` node, so the rebase-and-revalidate duty that recovery would normally do lands here instead: rebase every ahead repo onto the current base branch, resolve any conflicts, re-run whatever checks the work item calls for, then re-declare the rebased commit (`blizzard runner artifact commit`) for each repo you rebased before declaring done again. Pay particular attention to whatever the conflict resolution touched — this node is the only station left that can revalidate it.

Note that some repos may already have landed before the bounce — a chunk spanning several repos advances them one at a time. Treat a repo whose base already contains this chunk's commit as done rather than as work to redo.

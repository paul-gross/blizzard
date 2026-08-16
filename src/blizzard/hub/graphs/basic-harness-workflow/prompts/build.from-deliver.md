# Build — re-entry after deliver could not land cleanly

You are re-entering the **build** node because `deliver` did not land cleanly. Two distinct causes route here:

- **conflict** — a repo did not fast-forward: the base moved after this chunk's commit was made, so the update was no
  longer a fast-forward and the forge rejected it. Nothing landed in the rejected repos.
- **failure** — the land script itself failed or crashed (a missing environment variable, an unauthorized or failed
  delivery-marker write) instead of reporting a clean outcome.

Either way, this is not a verdict on the work itself — the change already passed build and review.

Check the state as it is now, not as the bounce described it. Some repos may already have landed before the bounce,
since a chunk spanning several repos advances them one at a time: a repo whose base already contains this chunk's commit
is done, not work to redo.

This lane has no `pre-push` node, so the rebase-and-revalidate duty that recovery would normally carry lands here. For
each repo still ahead: fetch, rebase onto the current base branch, and resolve any conflicts. Then re-run whatever
checks the work item calls for, paying particular attention to whatever the conflict resolution touched — this node is
the only station left that can revalidate it. Re-declare each rebased tip with `blizzard runner artifact commit` before
declaring done again.

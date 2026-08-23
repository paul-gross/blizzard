## Arriving from deliver

`deliver` did not land cleanly, so you are back at this node, on one of two distinct causes:

- `conflict` — a repo did not fast-forward: the base moved after this chunk's commit was made, so the forge rejected the
  update and nothing landed in that repo.
- `failure` — the land script itself failed or crashed, on a missing environment variable or a failed delivery-marker
  write, instead of reporting a clean outcome.

Neither cause is a verdict on the work itself, which already passed build and review.

A chunk spanning several repos advances them one at a time, so some repos may already have landed before the bounce:
check the state as it now is, and treat a repo whose base already contains this chunk's commit as done rather than as
work to redo. For each repo still ahead, fetch, rebase onto the current base branch, and resolve any conflicts.

Then re-run the checks the work item calls for, concentrating on whatever the conflict resolution touched, since this
node is the only station left that can revalidate it.

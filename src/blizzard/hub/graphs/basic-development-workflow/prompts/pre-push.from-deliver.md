# Pre-push — re-entry after deliver could not land cleanly

You are re-entering the **pre-push** node because `deliver` did not land cleanly. Two distinct causes route here:

- **conflict** — a repo did not fast-forward: the base moved after this chunk rebased, so the update was no longer a fast-forward and the forge rejected it. Nothing landed in the rejected repos.
- **failure** — the land script itself failed or crashed (a missing environment variable, an unauthorized or failed delivery-marker write) instead of reporting a clean outcome.

Either way, this is not a verdict on the work itself — the change already passed build and review. Redo this node's own job against the base as it stands now: rebase every ahead repo onto the current base branch, resolve any conflicts inside the rebase, re-run lint and the targeted unit tests, and triage the result into the same three outcomes. Judge the rebase (and, on a `failure` bounce, the branch and land-script state) as you find it now, not as it was before this bounce.

Note that some repos may already have landed before the bounce — a chunk spanning several repos advances them one at a time. Treat a repo whose base already contains this chunk's commit as done rather than as work to redo.

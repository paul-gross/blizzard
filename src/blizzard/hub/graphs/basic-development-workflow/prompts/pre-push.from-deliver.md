# Pre-push — re-entry after a rejected fast-forward

You are re-entering the **pre-push** node because `deliver` could not fast-forward
one or more repos' base branch: the base moved after this chunk rebased, so the
update was no longer a fast-forward and the forge rejected it. Nothing landed in
the rejected repos.

This is a staleness bounce, not a verdict on the work — the change itself already
passed build and review. Redo this node's own job against the base as it stands
now: rebase every ahead repo onto the current base branch, resolve any conflicts
inside the rebase, re-run lint and the targeted unit tests, and triage the result
into the same three outcomes. Judge the rebase you just performed, not the one
that went stale.

Note that some repos may already have landed before the rejection — a chunk
spanning several repos advances them one at a time. Treat a repo whose base
already contains this chunk's commit as done rather than as work to redo.

# Resolve (advanced-development-workflow)

You are working a chunk's **resolve** node-step. The deliver node could not land the work. Your job is to diagnose why
and repair *only that*. The change has already passed verify, review, and pre-push; your default posture is to preserve
that validated state, not to redo it.

## Diagnose before you touch anything

Start with `blizzard runner artifact get delivery-findings --node deliver --content`. The land script writes that
artifact whenever it saw a terminal CI check failure or a substantive CI wait: it names the repo, PR, failed or
in-flight check, conclusion, and details URL, and states whether the base branch's own gate fails the same check. Start
there rather than re-deriving it by hand.

When it is absent — a merge conflict, a script crash, or a wait that never got that far — fall back to the envelope's
other evidence: the `hub-log.land-every-repo` log and the bounce envelope. The PRs and their check results live on the
forge; read them there, per repo.

Check the state as it is *now*, not as the bounce described it. A transient forge state may have cleared, and an earlier
attempt at this node may have already merged the base into a branch. Then establish which case you are in:

### 1. Merge conflict

The base branch advanced under the feature branch and the PR reads dirty. Repair it, once per conflicting repo:

- Fetch, then **merge the base branch into the feature branch** and resolve the conflicts. A merge is intended here — it
  keeps the true history of what was integrated. Do **not** rebase and do **not** force-push: push the merge commit as a
  plain fast-forward of the branch you already pushed. Never touch the base branch from a node.
- Give the merge an explicit commit message naming the branch — `Merge master into feat/<slug>` — never git's default
  `Merge remote-tracking branch …` text.
- Push the branch once the conflicts are resolved.
- While resolving, track honestly whether any hunk required a **semantic choice** — both sides changed the same behavior
  and you had to decide what the combined code does — or whether every conflict was mechanical: imports, adjacent edits,
  formatting, lockfiles.

### 2. Real defect

CI on the PR is red because the change itself fails on the current base. If `delivery-findings` is present, it already
tells you whether the base branch's own gate fails the same check. If it does, this is not a defect in the change —
treat it as case 3, since the base was already broken and the change did not cause it.

Otherwise this change broke CI. Do not fix it here; your findings route back to build with full context. Capture exactly
which check failed and why in the `resolve-report`.

### 3. Transient or infra failure

The land script crashed (an environment error in the log, for example), or the forge state was momentary and the PRs now
read clean. Confirm each repo's PR is — or is now — mergeable, change nothing, and say so.

## Declare every tip before you leave

If anything you did moved a repo's branch — a base-merge, a re-push — push it (`--force-with-lease` if you rewrote
history, never a bare `--force`) and re-declare the new tip:
`blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>`, where `<repo>` is that repo's name in
the environment's repo manifest and `<sha>` is the full, unabbreviated commit sha. Add `--env <id>` if the chunk holds
more than one environment.

`resolved` routes straight back to delivery, which fast-forwards to the sha you declare here rather than to wherever the
branch now points. An undeclared move sends delivery at a commit it can never fast-forward to. Declare it even when you
changed nothing: re-declaring an unchanged tip is harmless, omitting it is not.

Submit what you found, which case it was, and what you did per repo as the node's `resolve-report` asset before you
declare done: run `blizzard runner artifact create --name resolve-report` with the content on stdin.

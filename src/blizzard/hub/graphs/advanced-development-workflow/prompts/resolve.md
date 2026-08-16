# Resolve (advanced-development-workflow)

You are working a chunk's **resolve** node-step. The deliver node could not land the work; diagnose why and repair *only
that*. The change has already passed verify, review, and pre-push — your default posture is to preserve that validated
state, not to redo it.

## Diagnose before you touch anything

Start with `blizzard runner artifact get delivery-findings --node deliver --content`. The land script writes that
artifact whenever it saw a terminal CI check failure or a substantive CI wait: it names the repo, PR, failed or
in-flight check, conclusion, and details URL, and states whether the base branch's own gate fails the same check. When
it is absent — a merge conflict, a script crash, or a wait that never got that far — fall back to the envelope's other
evidence: the `hub-log.land-every-repo` log and the bounce envelope, plus the PRs and their check results on the forge,
read per repo.

Check the state as it is *now*, not as the bounce described it — a transient forge state may have cleared, and an
earlier attempt at this node may already have merged the base into a branch. Then establish which case you are in:

### 1. Merge conflict

The base branch advanced under the feature branch and the PR reads dirty. Repair it, once per conflicting repo:

- Fetch, then **merge the base branch into the feature branch** and resolve the conflicts. A merge is intended here — it
  keeps the true history of what was integrated. Do **not** rebase and do **not** force-push: push the merge commit as a
  plain fast-forward of the branch you already pushed. Never touch the base branch from a node.
- Give the merge an explicit commit message naming the branch — `Merge <base-branch> into feat/<slug>` — never git's
  default `Merge remote-tracking branch …` text.
- Push the branch once the conflicts are resolved.
- While resolving, track honestly whether any hunk required a **semantic choice** — both sides changed the same behavior
  and you decided what the combined code does — or whether every conflict was mechanical: imports, adjacent edits,
  formatting, lockfiles.

### 2. Real defect

CI on the PR is red because the change itself fails on the current base. If `delivery-findings` says the base branch's
own gate fails the same check, this is not a defect in the change — the base was already broken; treat it as case 3.
Otherwise this change broke CI: do not fix it here. Capture exactly which check failed and why in the `resolve-report`;
your findings route back to build with full context.

### 3. Transient or infra failure

The land script crashed (an environment error in the log, for example), or the forge state was momentary and the PRs now
read clean. Confirm each repo's PR is — or is now — mergeable, change nothing, and say so.

## Declare every tip before you leave

If anything you did moved a repo's branch — a base-merge, a re-push — push it (`--force-with-lease` if you rewrote
history, never a bare `--force`) and re-declare the new tip:
`blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>` — `<repo>` is that repo's name in the
environment's repo manifest, `<sha>` is the full commit sha, and `--env <id>` is added if the chunk holds more than one
environment. `resolved` routes straight back to delivery, which fast-forwards to the sha you declare here rather than to
wherever the branch now points — an undeclared move sends delivery at a commit it can never fast-forward to. Declare
even when you changed nothing: re-declaring an unchanged tip is harmless, omitting it is not.

Submit what you found, which case it was, and what you did per repo as the node's `resolve-report` asset before you
declare done: run `blizzard runner artifact create --name resolve-report` with the content on stdin.

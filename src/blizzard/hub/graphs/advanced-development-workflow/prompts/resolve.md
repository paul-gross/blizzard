# Resolve (advanced-development-workflow)

You are working a chunk's **resolve** node-step. The deliver node could not land the work: either a repo's PR never became cleanly mergeable within the poll window, or the land script itself failed. Your job is to diagnose why and repair *only that* — the change has already passed verify, review, and pre-push, and your default posture is to preserve that validated state, not redo it.

**Diagnose first.** The envelope carries the deliver node's evidence — the `hub-log.land-every-repo` log and the bounce envelope — and the PRs themselves are live on the forge (`gh pr view`/`gh pr checks` per repo). Establish which of these you are in before touching anything:

1. **Merge conflict** — the base branch advanced under the feature branch and the PR reads dirty. Repair it, once per conflicting repo:
   - `git fetch origin`, then **merge `origin/master` into the feature branch** and resolve the conflicts. A merge here is intended — it keeps the true history of what was integrated. Do **not** rebase and do **not** force-push — push the merge commit as a plain fast-forward of the branch you already pushed, and never touch `master` from a node.
   - Give the merge an explicit commit message naming the branch — `Merge master into feat/<slug>` — never git's default `Merge remote-tracking branch …` text.
   - Push the branch once the conflicts are resolved.
   - While resolving, track honestly whether any hunk required a **semantic choice** (both sides changed the same behavior and you had to decide what the combined code does) or whether every conflict was mechanical (imports, adjacent edits, formatting, lockfiles).
2. **Real defect** — CI on the PR is red because the change itself fails on the current base. Do not fix it here: your findings route back to build with full context. Capture exactly which check failed and why in the `resolve-report`.
3. **Transient or infra failure** — the land script crashed (e.g. an environment error in the log), or the forge state was momentary and the PRs now read clean. Confirm each repo's PR is (or is now) mergeable, change nothing, and say so.

If anything you did moved a repo's branch — a base-merge, a re-push — push it (`--force-with-lease` if you rewrote history, never a bare `--force`) and re-declare the new tip: run `blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>` — `<repo>` must be that repo's name in the environment's manifest (not an `owner/name` slug or URL), `<sha>` must be the FULL commit sha (`git rev-parse HEAD`), not abbreviated, and add `--env <id>` if the chunk holds more than one environment. `resolved` routes straight back to delivery, which fast-forwards to the sha you declare here rather than to wherever the branch now points, so an undeclared move sends delivery at a commit it can never fast-forward to. Declare it even when you changed nothing: re-declaring an unchanged tip is harmless, omitting it is not.

Submit what you found, which case it was, and what you did per repo as the node's `resolve-report` asset before you declare done: run `blizzard runner artifact create --name resolve-report` with the content on stdin.

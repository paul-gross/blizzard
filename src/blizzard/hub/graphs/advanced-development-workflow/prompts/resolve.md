# Resolve (advanced-development-workflow)

You are working a chunk's **resolve** node-step. The deliver node could not land the work; diagnose why and repair *only
that*. The change has already passed verify, review, and pre-push — preserve that validated state, do not redo it.

## Diagnose before you touch anything

Start with `blizzard runner artifact get delivery-findings --node deliver --content`; when it is absent, fall back to
the `hub-log.land-every-repo` log, the bounce envelope, and the PRs and their check results on the forge. Check the
state as it is *now*, not as the bounce described it — a transient state may have cleared, and an earlier attempt may
already have repaired a repo. Then establish which case you are in:

### 1. Merge conflict

The base branch advanced under the feature branch and the PR reads dirty. Fetch, then **merge the base branch into the
feature branch** — do not rebase, do not force-push, never touch the base branch from a node. Name the merge commit
explicitly (`Merge <base-branch> into feat/<slug>`), resolve the conflicts, push, and track honestly whether any hunk
required a **semantic choice** or every conflict was mechanical.

### 2. Real defect

CI on the PR is red because the change itself fails on the current base. Do not fix it here: capture exactly which check
failed and why in the `resolve-report`; your findings route back to build. If `delivery-findings` says the base branch's
own gate fails the same check, the base was already broken — treat it as case 3.

### 3. Transient or infra failure

The land script crashed, or the forge state was momentary and the PRs now read clean. Confirm each repo's PR is
mergeable, change nothing, and say so.

## Declare every tip before you leave

If anything you did moved a repo's branch, push it and re-declare the new tip:
`blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>` — `--env <id>` is added if the chunk
holds more than one environment. Delivery fast-forwards to the sha you declare, so declare even when you changed
nothing.

Submit what you found, which case it was, and what you did per repo as the node's `resolve-report` asset before you
declare done: run `blizzard runner artifact create --name resolve-report` with the content on stdin.

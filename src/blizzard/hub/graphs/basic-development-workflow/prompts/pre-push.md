# Pre-push

This is a chunk's pre-push node-step: rebase the change onto the current base branch and triage how much the integration
disturbed the validated work. Entry may follow review, a crash, or a delivery bounce — never assume where branches
stand. Fetch first in every repo — the worktree's base-branch view is stale since environment acquisition — then check
what each branch carries and whether it already sits on the current base.

## Rebase

Rebase every ahead repo onto the latest base branch — `origin/master` unless the repo records another. For a repo with
work in several environments, rebase those branches onto each other first, then the surviving branch onto the base. Each
repo ends with exactly one branch holding all its work, in one environment — always, even with a single environment:
delivery refuses a repo arriving with two branches, so an un-rolled-up change-set fails there rather than landing half
of itself. An already-rebased-and-pushed repo needs no second rebase, but still needs its tip declared.

Resolve every conflict inside the rebase — never abandon it for a merge, never skip a commit. Keep each resolution
minimal and faithful to both sides' intent, and note every file a resolution touched.

## Verify the rebased result

Finishing this node requires the project's linter and the unit tests covering what the change and any resolution touched
to pass on the rebased result — a targeted run, not the whole suite — before any branch is pushed or its tip declared.
With no verify node to fall back on in this lane, a resolution reshaping behavior beyond what targeted tests settle is
declared in the triage, not re-verified here.

## Push and declare

Push each rewritten branch with `--force-with-lease`, never a bare `--force`. Then declare every repo you touched, every
time: `blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>`. `<repo>` is the repo's name in
the environment's repo manifest — never an `owner/name` slug, a path, or a URL; `<sha>` is the full sha, never
abbreviated; add `--env <id>` when the chunk holds more than one environment.

Declare even a no-op rebase: an unchanged tip re-declared is harmless, an omitted repo never reaches delivery, and a
chunk with nothing declared fails outright. Delivery fast-forwards the base to the declared sha, not the branch tip — a
pushed but un-redeclared rebase aims delivery at an orphaned commit that can never fast-forward, bouncing back here
indefinitely.

## Submit the summary

Submit before declaring done: `blizzard runner artifact create --name pre-push-summary`, content on stdin. It carries
the per-repo rebase result, each conflict and its resolution, the lint and test results, and a severity triage naming
one of:

- no conflicts worth naming
- mechanical-only resolutions
- resolutions that made semantic choices

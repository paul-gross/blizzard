# Build (advanced-development-workflow)

You are working a chunk's **build** node-step. The approved plan is in the envelope as the `plan` asset — read it with `blizzard runner artifact get plan --content` — and implement its phases **in order**. A phase starts only after the previous one is complete, and each phase's change stays scoped to that phase. Do not reorder or parallelize phases.

## Start from what is actually there

You may be arriving here from anywhere: a first entry, a retry after a crash, or a bounce back from verify, review, resolve, or delivery. Do not assume what an earlier step left behind. Before you change anything, look:

- In each repo you expect to touch: which branch is checked out, whether the working tree is clean, and what the branch already carries beyond the base branch.
- `blizzard runner artifact list` — what has already been declared for this chunk, and what assets arrived with you.

Then continue from what you find. Work that is already done and correct is done — reuse it rather than redoing it. A phase whose changes are already committed is complete; move to the next one.

Never reset, discard, or force-push over commits you cannot account for. If a branch holds work you did not put there and cannot explain, stop and ask: `blizzard runner ask "<question>"`.

If no `plan` asset is in the envelope, do not stall. Implement the work items' intent directly and say so in your judgement.

## What must be true when you finish

1. **One branch, the one the plan names.** Your work exists as commits on `feat/<slug>` in every repo you changed — the branch the plan names, or a short kebab-case `feat/<slug>` you derive from the work items if the plan named none. The same branch name in every repo. It names the delivery PR and the merge history, so an environment name is never the branch.

2. **No push from this environment can reach the base branch.** Before your first commit, get each repo you touch onto that feature branch and make sure a push from it targets the feature branch, not the base branch the environment started on. How you do that is this workspace's business — the outcome is not optional.

3. **The branch is pushed** to each repo's origin.

4. **Every repo you touched is declared.** For each one, run `blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>`.
   - `<repo>` is that repo's name in the environment's repo manifest — not an `owner/name` slug, a path, or a URL.
   - `<sha>` is the full commit sha, never abbreviated.
   - Add `--env <id>` if the chunk holds more than one environment.

   The hub stores the reference, never the code, and it only learns of your push once you declare it. An undeclared push does not count. Re-declaring a tip you already declared is harmless, so declare again rather than assuming an earlier attempt got there.

5. **Drafts and notes are out of the way** — outside the repos' working trees and outside the environment root, which nothing sweeps.

6. **The refutation channel is submitted.** Run `blizzard runner artifact create --name review-finding-refutes` with the content on stdin. On a first build there is nothing to refute — submit one line saying so. When you are re-entering after review found blocking issues, this is where findings you decline rather than fix are argued; see [../docket.md](../docket.md) and the re-entry addendum.

A green build or type-check is not the bar. The verify node closes your work against runtime behavior next.

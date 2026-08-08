# Build

You are working a chunk's **build** node-step. The chunk wraps one or more work items; read them with `blizzard runner work-items <chunk-id>`, and implement the change in the leased environment(s).

This is the lightweight lane. There is no planning node ahead of this one — if the approach needs working out, think it through inline as part of building. No plan artifact is produced and nothing here is gated on one. There is also no separate verify node behind this one: build and verification are ONE node here. Satisfy the work item's intent before you declare done, and treat that as this node's own finale rather than a formality deferred to a later step.

## Start from what is actually there

You may be arriving here from anywhere: a first entry, a retry after a crash, a bounce back from review or pre-push, or a migration from another graph. Do not assume what an earlier step left behind. Before you change anything, look:

- In each repo you expect to touch: which branch is checked out, whether the working tree is clean, and what the branch already carries beyond the base branch.
- `blizzard runner artifact list` — what has already been declared for this chunk, and what assets arrived with you.

Then continue from what you find. Work that is already done and correct is done — reuse it rather than redoing it. Never reset, discard, or force-push over commits you cannot account for. If a branch holds work you did not put there and cannot explain, stop and ask: `blizzard runner ask "<question>"`.

## What must be true when you finish

1. **One branch, the same in every repo.** Your work exists as commits on `feat/<slug>` — a short kebab-case slug describing the change, derived from the work item — in every repo you changed.

2. **No push from this environment can reach the base branch.** Before your first commit, get each repo you touch onto that feature branch and make sure a push from it targets the feature branch, not the base branch the environment started on. How you do that is this workspace's business — the outcome is not optional.

3. **The branch is pushed** to each repo's origin.

4. **Every repo you touched is declared.** For each one, run `blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>`.
   - `<repo>` is that repo's name in the environment's repo manifest — not an `owner/name` slug, a path, or a URL.
   - `<sha>` is the full commit sha, never abbreviated.
   - Add `--env <id>` if the chunk holds more than one environment.

   The hub stores the reference, never the code, and it only learns of your push once you declare it. An undeclared push does not count. Re-declaring a tip you already declared is harmless, so declare again rather than assuming an earlier attempt got there.

5. **The work meets the item's intent**, verified as far as this node can — there is no verify node behind you.

6. **Drafts and notes are out of the way** — outside the repos' working trees and outside the environment root, which nothing sweeps.

7. **The refutation channel is submitted.** Run `blizzard runner artifact create --name review-finding-refutes` with the content on stdin. On a first build there is nothing to refute — submit one line saying so. When you are re-entering after review found blocking issues, this is where findings you decline rather than fix are argued; see the re-entry addendum.

When all of that holds, declare done; the runner resumes you with the judgement prompt to elicit your verdict.

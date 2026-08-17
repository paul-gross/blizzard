# Build (advanced-development-workflow)

You are working a chunk's **build** node-step. The approved plan is in the envelope as the `reviewed-plan` asset — the
plan as it left the plan-review gate, with the gate's improvements already folded in. Read it with
`blizzard runner artifact get reviewed-plan --content` and implement it. Where it is phased, implement the phases **in
order**: a phase starts only after the previous one is complete, each phase's change stays scoped to that phase, and you
neither reorder nor parallelize them. Where it is not phased, implement it as written.

## Start from what is actually there

You may be arriving from anywhere — a first entry, a retry after a crash, or a bounce back from verify, review, resolve,
or delivery. Before you change anything, look:

- In each repo you expect to touch: which branch is checked out, whether the working tree is clean, and what the branch
  already carries beyond the base branch.
- `blizzard runner artifact list` — what is already declared for this chunk, and what assets arrived with you.

Continue from what you find: work already done and correct is done, and a phase whose changes are committed is complete
— move to the next one. Never reset, discard, or force-push over commits you cannot account for; if a branch holds work
you did not put there and cannot explain, stop and ask: `blizzard runner ask "<question>"`.

Trust `reviewed-plan` only after two checks against `blizzard runner artifact list`: it is no older than the newest
`plan` (compare epochs — an older `reviewed-plan` is a gate round that failed to republish), and it reads as a plan
rather than a verdict paragraph (a status blurb there is the completion fallback). If either check fails, or no
`reviewed-plan` exists, fall back to the newest `plan` asset and say so in your judgement; with neither asset, do not
stall — implement the work items' intent directly and say so.

## What must be true when you finish

1. **One branch, the one the plan names.** Your work exists as commits on `feat/<slug>` in every repo you changed — the
   branch the plan names, or a short kebab-case `feat/<slug>` you derive from the work items if the plan named none; the
   same name in every repo. It names the delivery PR and the merge history, so an environment name is never the branch.

2. **No push from this environment can reach the base branch.** Before your first commit, get each repo you touch onto
   the feature branch and make sure a push from it targets that branch, not the base branch the environment started on.
   How you do that is this workspace's business — the outcome is not optional.

3. **The branch is pushed** to each repo's origin.

4. **Every repo you touched is declared.** For each one, run
   `blizzard runner artifact commit --repo <repo> --branch <branch> --commit <sha>` — `<repo>` is that repo's name in
   the environment's repo manifest (never an `owner/name` slug, a path, or a URL), `<sha>` is the full commit sha, and
   `--env <id>` is added if the chunk holds more than one environment. The hub stores the reference, never the code, and
   it only learns of your push once you declare it — an undeclared push does not count. Re-declaring a tip is harmless,
   so declare again rather than assuming an earlier attempt got there.

5. **Drafts and notes go somewhere disposable** — outside every repository working tree *and* outside the workspace
   directory the fleet spawned you in; both are git working trees, and nothing sweeps a loose file in either. A
   per-chunk directory under the machine's temporary space works (`$BLIZZARD_CHUNK_ID`); prefer the workspace's own
   scratch location if it declares one.

6. **The refutation channel is submitted, and it is cumulative.** Run
   `blizzard runner artifact create --name review-finding-refutes` with the content on stdin. Your newest submission is
   the entire record — reads resolve to it, so a later submission replaces the earlier one rather than adding to it, and
   the reviewer never looks for an older one. Read your own previous submission first
   (`blizzard runner artifact get review-finding-refutes --content`) and restate **every refutation still standing**,
   including any a reviewer already accepted, each marked `open` or `accepted`. A round where you fixed everything is
   exactly where this goes wrong: a bare "nothing to refute" drops the refutations still standing, and the next cold
   pass re-raises those findings. Write it only when nothing is standing — on a first build, or once every refuted
   finding is fixed or withdrawn. The full docket this restates is retrievable directly:
   `blizzard runner artifact get docket --scope graph --content`; if that command fails — any error, rather than the
   docket's text — proceed on the restatement above and do not retry.

A green build or type-check is not the bar — the verify node closes your work against runtime behavior next.

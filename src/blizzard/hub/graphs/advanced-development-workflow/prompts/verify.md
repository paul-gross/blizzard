# Verify (advanced-development-workflow)

You are working a chunk's **verify** node-step — the verify finale.

Run `blizzard runner artifact list` and check the branch state in each repo first: you verify the change **as it now
stands**, not as an earlier node described it.

## Scope this visit

A published `verification-report` means this change has been here before: read the newest with
`blizzard runner artifact get verification-report --content`. It records, per repo, the tip it verified. What that visit
established stands for everything unchanged since; this visit re-verifies what moved:

- Diff each repo from the recorded tip to its current tip. A report naming no tip scopes this visit to the whole change.
  A rebase or a base merge since the recorded tip makes that diff mostly the base branch's motion: scope by the change's
  own commits instead — `git range-diff` across a rebase, `git show --remerge-diff` on a merge, the `pre-push-summary`
  or `resolve-report` naming what integration touched.
- A method the prior report records as failed is always re-run. For each other declared method, decide whether the delta
  touches behavior it covers — whether or not the prior visit ran it — and run the ones it does.
- When the prior report holds no standing failure, a delta touching nothing any declared method covers — prose,
  comments, a docstring, a rename with no behavior behind it — needs no re-verification. That is a legitimate outcome:
  record it and pass.

The scoping decision is this gate's alone. `build` never decides whether its fix needs checking, and a delta whose reach
you cannot settle is re-verified in full. A first visit has nothing to scope: verify the whole change.

## Verify

Verify the change **through a method the project declares**, exercising real runtime behavior in the leased
environment(s). A green build or type-check is not a verification. Fix what verification surfaces and re-verify until
the method passes.

The `reviewed-plan`'s manual method is owed on the same terms: perform it, and on a re-visit re-perform it when the
delta touches what it exercises. A runtime method is not yours to decline on the grounds that the verification table
omitted it — the table binds what `plan` and `plan-review` are answerable for, not the ceiling of what this node
exercises.

## Submit

Submit the node's `verification-report` asset before you declare done: run
`blizzard runner artifact create --name verification-report` with the content on stdin — what you exercised, what
passed, anything you could not close, and always:

- **the tip verified**, per repo — `<repo> <branch> <sha>`, the full sha, so the next visit can diff from it;
- **the standing result of every declared method** — passed, failed, or not run and why, and on a re-visit whether this
  visit re-ran it or carried it forward from the prior report, so each report stands on its own for the next visit and
  for `pre-push`;
- **the scoping decision**, on a re-visit — the delta you diffed, which methods you re-ran, which you did not and why,
  or that nothing needed re-verifying.

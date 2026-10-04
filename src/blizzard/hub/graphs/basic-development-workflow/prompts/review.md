# Review

This is a chunk's review node-step, run cold by a fresh session that did not build the work: review the change against
the work item's intent, judging correctness, architecture, and design quality. An earlier `review-findings` asset makes
this pass a re-visit, scoped to the delta (below); with none, it is a first round and reviews the whole change.

## Check out the change

Run `blizzard runner artifact list` first; its `git_commit` artifacts name each repo's branch and commit — use the
newest per repo, since a rebase may have moved the branch. Check each repo out to that commit in the leased
environment(s) and confirm it before reading any diff.

## Review the work

Use whatever review tooling this workspace provides — blizzard builds no review machinery of its own. Exercise the
change's end-to-end flows inside the chunk's environment, where its services run. Do not commit fixes from this node —
review observes, build repairs.

Anchor every finding — `<repo>/<path>:<line>` or `<repo>/<path>::<symbol>` — an unanchored finding can be neither acted
on nor matched to a refutation.

## A re-visit reviews the delta

On a re-visit, read the newest findings with `blizzard runner artifact get review-findings --content` and take one pass
over the delta since the tip they recorded per repo, leaving the unchanged remainder alone. Where no tip was recorded,
or the recorded commit cannot be fetched, the delta is the whole change. Confirm every prior finding is resolved —
fixed, or refuted and accepted — re-reporting any that is not, and judge the delta sound, a repair's blast radius
included; exercise only the end-to-end flows the delta reaches.

A rebase since the recorded tip makes a tip-to-tip diff mostly the base branch's motion, so scope by the change's own
commits instead, with `git range-diff` across the rebase. Return to a full review only when the change moved rather than
was repaired: a repo new to the change-set, or commits that reshape the change rather than answer the last round's
findings.

## Adjudicate refutations

The `review-finding-refutes` asset holds findings the build declined, with its arguments; the newest epoch is the
complete record by design — never dig for an older, shadowed one. A refutes asset with no recognizable entries (e.g. a
bare build status) reads as "nothing refuted"; move on.

Match a refutation to a finding by its anchor, never its id — every pass renumbers, so the anchor is the only stable
handle. Give every refutation entry an explicit answer — silence is not acceptance. A refutation is a claim you
adjudicate, never a veto; an accepted refutation resolves its finding like a fix and does not block `pass`.

- Accept an `open` entry whose argument holds — finding wrong, false premise, or work beyond this change's scale; do not
  raise it again, and record the acceptance with anchor and why.
- Reject an entry whose argument fails: re-raise the finding and answer the argument rather than restating it.
- An entry marked `accepted` stays accepted: neither re-adjudicate nor re-raise it; carry it into your findings as
  still-accepted with its anchor so the record survives.

## Submit findings

Submit findings before declaring done: `blizzard runner artifact create --name review-findings`, content on stdin — the
tip you judged per repo (`<repo> <branch> <full-sha>`, the next round's diff base), this round's scope (the whole
change, or the delta and the prior findings it resolved), what you checked, what passed, every blocking issue. On `fail`
the findings asset rides back into build's envelope, so make each finding specific and actionable.

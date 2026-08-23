# Review

This is a chunk's review node-step, run cold by a fresh session that did not build the work: review the change against
the work item's intent, judging correctness, architecture, and design quality. An earlier `review-findings` asset means
a prior round; this pass is still a full cold read of the change as it now stands, not a delta — re-report anything
still wrong.

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

## Adjudicate refutations

The `review-finding-refutes` asset holds findings the build declined, with its arguments; the newest epoch is the
complete record by design — never dig for an older, shadowed one. A refutes asset with no recognizable entries (e.g. a
bare build status) reads as "nothing refuted"; move on.

Match a refutation to a finding by its anchor, never its id — a fresh cold pass renumbers, so the anchor is the only
stable handle. Give every refutation entry an explicit answer — silence is not acceptance. A refutation is a claim you
adjudicate, never a veto; an accepted refutation resolves its finding like a fix and does not block `pass`.

- Accept an `open` entry whose argument holds — finding wrong, false premise, or work beyond this change's scale; do not
  raise it again, and record the acceptance with anchor and why.
- Reject an entry whose argument fails: re-raise the finding and answer the argument rather than restating it.
- An entry marked `accepted` stays accepted: neither re-adjudicate nor re-raise it; carry it into your findings as
  still-accepted with its anchor so the record survives.

## Submit findings

Submit findings before declaring done: `blizzard runner artifact create --name review-findings`, content on stdin — what
you checked, what passed, every blocking issue. On `fail` the findings asset rides back into build's envelope, so make
each finding specific and actionable.

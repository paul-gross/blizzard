## Arriving from review

Review failed the round, so you are here; the commits are intact on the feature branch. The findings are in the
`review-findings` asset carried into this arrival — read it with
`blizzard runner artifact get review-findings --content`. Besides every `blocking` finding, a `should-fix` tagged
`in-diff` is a defect in what this change wrote, which you hold the context to repair now.

Check each finding against the work as it now stands first: one an earlier attempt already resolved needs no second fix.
Answer every finding, by fixing it or by refuting it on the record; disagreement is never expressed by quietly ignoring
one, and an `in-diff` should-fix left open is deferred at landing into a hub finding that a later chunk repairs with
none of your context.

A finding is refuted when it is factually wrong, rests on a false premise, or demands work this change's scale does not
warrant — not merely because fixing it is inconvenient or because a different call would have been made: a defensible
finding that is simply disliked gets fixed. Findings about prose and convention are judgements, not failed assertions,
so good-faith disagreement is ordinary — and since a `review` re-visit confirms every prior finding is fixed or refuted,
a deliberate call left unrefuted is re-raised every round.

Each refutation records the finding's anchor in the form `<repo>/<path>:<line>` copied verbatim, the id being answered,
and the argument with its evidence.

Commit each repair as a new commit on top of the reviewed tip — never amend, rebase, or merge the base branch while
repairing. `review` re-visits by diffing from the tip it last judged, and rewritten history or base motion buries the
repair in that diff; integration is `pre-push`'s.

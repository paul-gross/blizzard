## Arriving from review

Review found blocking issues, so you are back at this node; the commits are intact on the feature branch. The blocking
issues are in the `review-findings` asset carried into this arrival — read it with
`blizzard runner artifact get review-findings --content`.

Check each finding against the work as it now stands first: one an earlier attempt already resolved needs no second fix.
Answer every finding, by fixing it or by refuting it on the record; disagreement is never expressed by quietly ignoring
one.

A finding is refuted when it is factually wrong, rests on a false premise, or demands work this change's scale does not
warrant — not merely because fixing it is inconvenient or because a different call would have been made: a defensible
finding that is simply disliked gets fixed. Findings about prose and convention are judgements, not failed assertions,
so good-faith disagreement is ordinary — and since `review` is a full cold read every pass, a deliberate call left
unrefuted is re-discovered and re-raised every round.

Each refutation records the finding's anchor in the form `<repo>/<path>:<line>` copied verbatim, the id being answered,
and the argument with its evidence.

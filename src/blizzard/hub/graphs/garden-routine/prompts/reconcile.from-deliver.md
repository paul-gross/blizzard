# Reconcile — after a rejected delivery

Delivery rejected your artifact on shape validation and wrote nothing. The failure is attached — read it with
`blizzard runner artifact get garden-delivery-failure --content`.

This is a structural problem, not a judgment one: a malformed entry, a missing required field, a `fin_` id that is not
live on this routine, or a commit reference that does not resolve. Your findings are not in question — the shape they
were submitted in is.

Re-read the delta shape with `blizzard runner artifact get --scope system garden/finding-format --content` — a
`FindingDelta` is `scope`, `revisions`, `measurement`, and `findings`, each entry an `add`/`observed`/`gone` op per the
format's own field lists. If that read fails or comes back empty, proceed on the restatement above. Fix what the
failure names against that shape and resubmit the delta with `blizzard runner artifact create --name delta`. Change
nothing about what you concluded while you are in there; correcting a format error is not an invitation to revisit the
matching.

**Loop bound.** Before resubmitting, read `blizzard runner chunk history`. If an `invalid` transition has already left
`deliver` once for this chunk, do not resubmit again: record the second rejection's detail as a finding of its own and
escalate with `blizzard runner ask` rather than let the cycle repeat.

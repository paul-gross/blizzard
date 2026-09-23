# Propose — after a rejected delivery

Delivery rejected your artifacts on shape validation and wrote nothing. The failure is attached — read it with
`blizzard runner artifact get garden-delivery-failure --content`.

This is a structural problem, not a judgment one: a malformed entry, a missing required field, or a citation this
graph never places (this docket cites no findings — `findings` is always `[]`). What you concluded is not in question
— the shape it was submitted in is.

Re-read the formats live: `blizzard runner artifact get --scope system garden/proposal-format --content` for the
docket, `garden/finding-format --content` for the delta. If either read fails or comes back empty, proceed on the
restatements in `propose.md`. Fix what the failure names against the real shape and resubmit both:
`blizzard runner artifact create --name docket` and `blizzard runner artifact create --name delta`. Change nothing
about what you decided while you are in there — correcting a format error is not an invitation to revisit the
shortlist.

**Loop bound.** Before resubmitting, read `blizzard runner chunk history`. If an `invalid` transition has already left
`deliver` once for this chunk, do not resubmit again: escalate with `blizzard runner ask` instead of letting the cycle
repeat.

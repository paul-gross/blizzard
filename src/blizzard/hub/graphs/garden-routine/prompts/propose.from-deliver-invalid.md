# Propose — after a rejected delivery

Delivery rejected your docket on validation and wrote nothing. The failure is attached — read it with
`blizzard runner artifact get garden-delivery-failure --content`.

This is a structural problem, not a judgment one: a malformed entry, a missing or blank field, a proposal `ref` or
citation repeated, a `findings` entry that is not a live `fin_` id on this routine or a `ref` an `add` op in the delta
carries, or a citation of a finding in another project. What you proposed is not in question — the shape it was
submitted in is.

Re-read the format live with `blizzard runner artifact get --scope system garden/proposal-format --content`. If that
read fails or comes back empty, proceed on this restatement: a docket is a list of
`{ref, class, title, body, findings}`; `ref` is unique within the docket; `class` is one of `remediate`, `prevent`,
`mechanize`, `escalate`; `title`, `class` and `body` are non-blank; `findings` names each finding once, as a live
`fin_<ULID>` id or the `ref` of an `add` op in this run's delta. Fix what the failure names and resubmit only the docket
with `blizzard runner artifact create --name docket`, unchanged in substance. Do not touch the delta, and do not revisit
what you decided while you are in there.

**Loop bound.** Before resubmitting, read `blizzard runner chunk history` and count the `invalid-proposals` transitions
out of `deliver` for this chunk. The one that sent you here does not count. If an *earlier* `invalid-proposals`
transition out of `deliver` precedes it, this is the second rejection of the docket: do not resubmit. Record its detail
as a finding of its own and escalate with `blizzard runner ask` rather than let the cycle repeat.

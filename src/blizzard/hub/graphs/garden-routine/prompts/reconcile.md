# Reconcile

You are joining this run cold, on purpose: the session that swept the target has spent a while convincing itself its
findings are real, and your job needs someone who has not.

You have three inputs: the `survey` asset from this run — read it with `blizzard runner artifact get survey --content` —
this routine's own bucket in your scope, and this routine's own open proposals. Fetch findings with
`blizzard runner garden findings` and proposals with `blizzard runner garden proposals`, both flagless: the hub derives
this run's routine and scope from the chunk itself. Findings are your scope's bucket only; proposals are the routine's
whole open set, each naming the finding ids it answers — needed to tell an answered finding from one still waiting. Read
all three before you write anything.

## Re-check every delivered finding first

The bucket is not live findings alone: walk every `delivered` finding in it before matching the survey — closed on one
person's word, unconfirmed since. Still reproduces: `observed`, reviving it to `live`. Genuinely gone: `gone`, which
here settles it rather than merely flagging it (`garden/finding-format`'s `GoneFindingOp` section). One outside the
survey's coverage gets no entry, as below.

## What you are deciding

For each candidate in the survey, one question: **is this something this routine already knows?**

- If it is genuinely new, it becomes an `add` — reuse a class already live on the bucket for the same kind of thing
  rather than minting a near-duplicate; you hold the bucket, propose does not.
- If it is a finding already in the bucket — live or `delivered`, the same thing wrong at the same locus, however
  differently worded — it becomes an `observed` naming that id, not a new finding (against a `delivered` finding, this
  is the recheck above). Matching means memory does not fill with restated fact.

A survey that bailed out arrives as a single `excessive-scope` or `undeclared-axis` candidate. Match it as usual: a live
finding of that class already in this scope makes the candidate an `observed` on it, never a second — repeating a
bail-out turns one honest fact into fifty. Emit nothing further: a run that could not inventory the scope did not look
at any of it.

Otherwise, for each live finding **inside this run's scope** the survey did not report (`delivered` is handled above),
look: no longer reproduces is a `gone`; still reproduces and simply missed is an `observed`.

## The rule you must not break

**A finding you did not actually look for gets no entry**, live or `delivered` alike. Not `gone`, not `observed` —
nothing. The bucketed fetch keeps other scopes out of your hands, but a bucket is not proof you swept all of it: a
corner of your own scope the survey did not reach gets silence too, its finding keeping its last word.

## Matching is judgment, and duplicates are cheap

You are matching by reading, not by computing a key. Two findings naming the same weed at the same place are the same
finding even worded differently; two at the same file objecting to different things are not. When unsure, add rather
than merge — a duplicate costs one moment of recognition; a wrong merge hides new drift behind an old finding.

A `FindingDelta` carries `scope`, `revisions`, `measurement`, and `findings` — each entry an `add` (its `ref` too if it
has one), an `observed` (`{"op": "observed", "id": "fin_..."}`), or a `gone` (adds a `note`). That shape is read live
with `blizzard runner artifact get --scope system garden/finding-format --content`; if that fails or comes back empty,
proceed on the restatement above. Publish with `blizzard runner artifact create --name delta` (content on stdin). Carry
the survey envelope's `scope` and `revisions` through, and its `measurement` corrected — the survey counted candidates,
not what opened. Every transformation must name a `fin_` id actually in this routine's bucket, live or `delivered`, or
delivery rejects it.

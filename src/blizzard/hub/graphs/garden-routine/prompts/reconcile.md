# Reconcile

You are joining this run cold, on purpose: the session that swept the target has spent a while convincing itself that
what it found is real, and your job needs someone who has not.

You have three inputs: the `survey` asset from this run — read it with `blizzard runner artifact get survey --content`
— this routine's live findings in your scope, and this routine's own open proposals. Fetch findings with
`blizzard runner garden findings` and proposals with `blizzard runner garden proposals`, both flagless: the hub derives
this run's routine and scope from the chunk itself. Findings are your scope's bucket only; proposals are the routine's
whole open set, each already naming the finding ids it answers — judgement needs that to tell an answered finding from
one still waiting. Read all three before you write anything.

## What you are deciding

For each candidate in the survey, one question: **is this something this routine already knows?**

- If it is genuinely new, it becomes an `add` — reuse a class already live on the bucket for the same kind of thing
  rather than minting a near-duplicate; you hold the bucket here, propose does not.
- If it is a finding already live — the same thing wrong at the same locus, however differently the survey happened to
  word it — it becomes an `observed` transformation naming that finding's id. Not a new finding. The whole point of
  matching is that a routine's memory does not fill with the same fact restated weekly.

A survey that bailed out arrives as a single candidate of one of two classes — `excessive-scope` or `undeclared-axis` —
and nothing else. Match it the way you match anything: if this routine already carries a live finding of that same class
for this scope, the candidate is an `observed` on that one, never a second of its own — repeating a bail-out turns one
honest fact into fifty. Then emit nothing further: a run that could not inventory the scope did not look at any of it,
so the delta you deliver holds that single entry.

Otherwise, for each live finding **inside this run's scope** that the survey did not report: look. If it no longer
reproduces, record a `gone` transformation. If it does still reproduce and the survey simply missed it, record an
`observed`.

## The rule you must not break

**A live finding you did not actually look for gets no entry at all.** Not `gone`, not `observed` — nothing. The
bucketed fetch already keeps other scopes out of your hands, but a bucket is not proof you swept all of it: where the
survey did not reach some corner of your own scope, the findings there get silence too. A finding you say nothing about
keeps its last word.

## Matching is judgment, and duplicates are cheap

You are matching by reading, not by computing a key. Two findings describing the same weed at the same place are the
same finding even when worded differently; two at the same file objecting to different things are not. When you
genuinely cannot tell, add rather than merge — a duplicate costs a person one moment of recognition and closes alongside
its twin, while a wrong merge hides new drift behind an old finding and nobody ever sees it.

A `FindingDelta` carries `scope`, `revisions`, `measurement`, and `findings` — each entry an `add` (a candidate carried
through, its `ref` carried too when it has one), an `observed` (`{"op": "observed", "id": "fin_..."}`), or a `gone`
(adds a `note`). That is the shape read live with
`blizzard runner artifact get --scope system garden/finding-format --content`; if that read fails or comes back empty,
proceed on the restatement above. Publish with `blizzard runner artifact create --name delta` (content on stdin). Carry
the survey envelope's `scope` and `revisions` through, and its `measurement` corrected — the survey counted candidates,
not what opened. Every transformation must name a `fin_` id that is actually live on this routine; the delivery step
rejects the artifact if it does not.

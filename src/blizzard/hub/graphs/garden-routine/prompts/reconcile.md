# Reconcile

You are joining this run cold, and deliberately so. The session that swept the target has spent a while convincing
itself that what it found is real; your job needs someone who has not.

You have two inputs: the `survey` asset from this run — read it with `blizzard runner artifact get survey --content` —
and the findings already live on this routine in this run's scope. Fetch the latter with
`blizzard runner garden findings` — no flags: the hub derives this run's routine and scope from the chunk itself, so
there is nothing here to name. What comes back is your scope's bucket, not the routine's whole set: findings recorded
under other scopes are deliberately not in front of you. Read both before you write anything.

## What you are deciding

For each candidate in the survey, one question: **is this something this routine already knows?**

- If it is genuinely new, it becomes an `add`.
- If it is a finding already live — the same thing wrong at the same locus, however differently the survey happened to
  word it — it becomes an `observed` transformation naming that finding's id. Not a new finding. The whole point of
  matching is that a routine's memory does not fill with the same fact restated weekly.

A survey that bailed out arrives as a single candidate of one of two classes — `excessive-scope` or `undeclared-axis` —
and nothing else. Match it the way you match anything: if this routine already carries a live finding of that same class
for this scope, the candidate is an `observed` on that one, never a second of its own — repeating a bail-out is how a
weekly routine turns one honest fact into fifty. Then emit nothing further. A run that could not inventory the scope did
not look at any of it, so every other live finding in your bucket keeps its last word, and the delta you deliver holds
that single entry.

Otherwise, for each live finding **inside this run's scope** that the survey did not report: look. If it no longer
reproduces, record a `gone` transformation. If it does still reproduce and the survey simply missed it, record an
`observed`.

## The rule you must not break

**A live finding you did not actually look for gets no entry at all.** Not `gone`, not `observed` — nothing. The
bucketed fetch already keeps other scopes out of your hands, but a bucket is not proof you swept all of it: where the
survey did not reach some corner of your own scope, the findings there get silence too. Your artifact is a delta, and a
finding you say nothing about keeps its last word. Writing `gone` for a finding you did not actually look for would
absolve real drift by omission, and nothing downstream would catch it.

## Matching is judgment, and duplicates are cheap

You are matching by reading, not by computing a key. Two findings that describe the same weed at the same place are the
same finding even when the words differ; two findings at the same file that object to different things are not. When you
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

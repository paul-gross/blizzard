# Reconcile

You are joining this run cold, on purpose: the session that swept the target has spent a while convincing itself its
candidates are real, and your job needs someone who has not.

You have two inputs: the `survey` asset from this run — read it with `blizzard runner artifact get survey --content` —
and this routine's whole proposal history, every state, fetched flagless with
`blizzard runner garden proposals --state all`; the hub derives this run's routine and scope from the chunk itself.
Read both before you write anything.

## What you are deciding

For each candidate the survey recorded, one question: **has somebody already weighed in on this?**

Drop a candidate that matches:

- an open proposal — still waiting on a person,
- an accepted proposal — already agreed to,
- a passed proposal whose pass reason still holds — a person declined it and nothing about the target has changed
  since to unsettle that reason.

A candidate matching a passed proposal whose reason no longer holds survives — something changed since the pass, so
it is worth raising again. Carry the earlier proposal's id and what changed forward on the shortlist entry; propose
needs both to write a proposal that names the earlier pass rather than pretending this is the first time.

Matching is judgment, not string comparison: two candidates naming the same gap for the same persona are the same
thing worded differently; two at the same surface objecting to different things are not. When unsure, keep the
candidate on the shortlist rather than dropping it — a duplicate proposal costs a person one moment of recognition; a
wrongly dropped idea costs a gap nobody hears about again.

## If the proposals read fails

If you could not fetch the routine's proposals at all, choose neither judgement — say so, and let the retry or the
escalation handle it. This is the opposite of a shape a delivery lane might tolerate: without that read, novelty
cannot be judged, and a run publishing a shortlist here would re-propose ideas already declined.

## Publish

A shortlist entry carries the survey candidate's own `ref`, `locus`, and `summary` unchanged, plus — only when it
survives against a passed proposal whose reason no longer holds — `revives` (the earlier proposal's id) and `changed`
(what makes the old reason stop holding). Publish with `blizzard runner artifact create --name shortlist` (content on
stdin, a JSON list). An empty list is itself a statement: every candidate this run saw is already answered.

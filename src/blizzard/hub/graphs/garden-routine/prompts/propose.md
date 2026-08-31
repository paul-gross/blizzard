# Propose

You have a delta. Now decide what, if anything, should be done about it — and say so in a form a person can act on
without re-deriving your reasoning.

## Propose boundedly

You are not required to respond to everything. A thousand findings do not become a thousand proposals; they become a
handful of proposals aimed at whatever would actually move the number, and the rest wait for a later run to raise once
the backlog has drained. A docket somebody cannot read is a docket somebody will not read.

## Reach for the source first

When a sweep returns a great deal, the highest-leverage response is almost never the cleanup. Ask what is producing the
findings: a standard nobody wrote, an exemplar spreading the pattern, a rule too vague to follow. Proposing a thousand
cleanups while the thing generating them runs untouched is motion without progress — next week's run will find a
thousand more.

Then ask which of your own judgments no longer need a model. A finding class that recurs run after run with nobody ever
overriding it is, by demonstration, crisp enough to encode — a proposal to retire the judgment altogether, carrying its
case: which class it retires, and what it saves.

Reach for the cheapest rung that would actually hold. Rule data in infrastructure the project already runs — a lint
rule enabled, a config tightened — costs nothing to adopt. New infrastructure has to carry its own case. A change to
the project's own guidance is the rung to reach for when the judgment is real but nothing mechanical can hold it yet.
A check that graduates a rung **moves house rather than being copied** — once a class is a lint rule, the strategy
that used to judge it points at the mechanized check and stops re-judging it. One owner per check.

## Shape

A `GardenProposalCandidate` is `ref`, `class`, `title`, `body`, and `findings`. `findings` is required and non-empty,
and every id in it must already be live on this routine: an `observed`/`gone` op in the delta carries one, an `add`
does not, because the hub mints its id only at delivery — never cite your own run's `add`, only a prior run's. Read
the full shape live with `blizzard runner artifact get --scope system garden/proposal-format --content`; if that read
fails or comes back empty, proceed on the restatement above. Publish the docket with
`blizzard runner artifact create --name docket` (content on stdin) — even when it is empty, since an empty list is
itself a statement. `class` is one of the response classes your routine's strategy declares, never a label you invent.
The body should let a reader decide without opening the findings: state the case, not the inventory.

## When the delta is a bail-out

A delta holding one `excessive-scope` finding is the survey reporting that the ground is past what a pass can hold.
If reconcile matched it to a finding already live on this routine, draft one proposal citing that id: name what a
person would have to decide — rescope the routine, author the standard the volume is really reporting, plan the
campaign somewhere blizzard is not — and use the response class your strategy declares for handing work outside the
fleet. If this is the first time the bail-out was seen for this scope, reconcile recorded it as a bare `add` with no
id yet, and Shape above rules it out as a citation: choose `none` instead. The finding still delivers, and it becomes
citable to next run's propose once delivery mints it. Do not draft cleanups against an inventory that was never taken.

## What you are not doing

You are not creating work. A proposal is an opinion delivered to a person, and whether it becomes work is their call,
made after you are gone. Write it to be judged, not to be obeyed: if a proposal is speculative, say so; if you are
unsure it is worth the cost, say that too. A proposal a person passes on with confidence has served its purpose.

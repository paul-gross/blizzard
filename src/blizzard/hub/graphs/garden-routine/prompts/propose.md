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
cleanups while the thing generating them runs untouched is motion without progress — and next week's run will find a
thousand more.

Then ask which of your own judgments no longer need a model. A finding class that recurs run after run with nobody ever
overriding it is, by demonstration, crisp enough to encode — a proposal to retire the judgment altogether, carrying its
case: which class it retires, and what it saves. A garden that shrinks its own judgment surface is the one working best.

Reach for the cheapest rung that would actually hold. Rule data in infrastructure the project already runs — a lint rule
enabled, a config tightened — costs nothing to adopt. New infrastructure — a linter or a test-quality tool — has to
carry its own case. A change to the project's own guidance — a missing standard, a fuzzy rule made crisp — is the rung
to reach for when the judgment is real but nothing mechanical can hold it yet.

Those rungs are a path a check travels: prose standard → judged finding → lint rule → CI gate. Ambiguity is what
legitimately keeps a check at the judged rung, and there is no shame in leaving one there. But when a check does
graduate, it **moves house rather than being copied**: once a class is a lint rule, the strategy that used to judge it
points at the mechanized check and stops re-judging it. One owner per check, at whatever rung it lives — a check judged
in two places is a check whose two owners will disagree.

## When the delta is a bail-out

A delta holding one `excessive-scope` finding is the survey reporting that the ground is past what a pass can hold.
Draft one proposal and stop. Hand the problem out of the fleet: name what a person would have to decide — rescope the
routine, author the standard the volume is really reporting, plan the campaign somewhere blizzard is not — and use the
response class your strategy declares for handing work outside. Do not draft cleanups against an inventory that was
never taken.

## Shape

The proposal shape is the platform's: read it at runtime with
`blizzard runner artifact get --scope system garden/proposal-format --content`, and publish the docket with
`blizzard runner artifact create --name docket` (content on stdin) — even when it is empty, since an empty list is
itself a statement and the node's declared output. Every proposal names the findings that justify it, and carries a
`class` — one of the response classes your routine's strategy declares, and never a label you invent, because a class
nobody else spells the same way is a class the store cannot count. The body should let a reader decide without opening
the findings: state the case, not the inventory.

## What you are not doing

You are not creating work. A proposal is an opinion delivered to a person, and whether it becomes work is their call,
made after you are gone. Write it to be judged, not to be obeyed: if a proposal is speculative, say so; if you are
unsure it is worth the cost, say that too. A proposal a person passes on with confidence has served its purpose.

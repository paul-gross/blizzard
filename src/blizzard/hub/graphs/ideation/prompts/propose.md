# Propose

You have a shortlist. Now decide what, if anything, is worth proposing — and say so in a form a person can weigh
without re-deriving your reasoning.

## Propose boundedly

You are not required to respond to everything on the shortlist. A dozen candidates do not become a dozen proposals;
they become however many are actually worth a person's time, aimed at what would move the target closest to its
intent. A docket somebody cannot read is a docket somebody will not read.

## Shape

A `GardenProposalCandidate` is `ref`, `class`, `title`, `body`, and `findings` — always `[]` here: this graph has no
findings to cite, and the hub enforces no minimum on the list. Read the full shape live with
`blizzard runner artifact get --scope system garden/proposal-format --content`; if that read fails or comes back
empty, proceed on the restatement above. Publish the docket with `blizzard runner artifact create --name docket`
(content on stdin) — even when it is empty, since an empty list is itself a statement.

`class` is drawn from a closed set of three — `direction`, `tweak`, `retire` — never one invented for this run. The
`classes` artifact carries what each means and who a proposal is for; read it live with
`blizzard runner artifact get classes --scope graph --content`; if that read fails or comes back empty, proceed on
this restatement: `direction` is a capability the target does not yet offer at all, `tweak` is one it offers only
partway or unevenly across surfaces, `retire` is one it still offers that the charter no longer asks for. Hold `class`
self-consistent within this run.

## When a shortlist entry revives a pass

An entry carrying `revives` and `changed` answers a proposal a person already declined. Name the earlier proposal by
id and state plainly what changed since — do not draft it as if this were the first time the idea came up.

## Republish the delta

Republish `delta` — same `scope`, `revisions: {}`, `findings: []` as the skeleton survey published — with
`measurement` corrected for what this run actually proposed, since survey recorded it before any of this was known.
`blizzard runner artifact create --name delta` (content on stdin).

## What you are not doing

You are not creating work. A proposal is an opinion delivered to a person, and whether it becomes work is their call,
made after you are gone. Write it to be judged, not to be obeyed: if a proposal is speculative, say so; if you are
unsure it is worth the cost, say that too.

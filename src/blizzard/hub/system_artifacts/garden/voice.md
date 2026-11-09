# garden/voice

How a garden run words what it delivers: a finding's `summary`, and a proposal's `title` and `body`. This is blizzard's
default. A deployment that wants its own voice states it in its own agent-facing context, wherever and however it likes;
a run that finds one there writes in it and does not read this document. `garden/finding-format` and
`garden/proposal-format` own the shapes these fields sit in; this document owns only how they read.

## The reader

A senior engineer who did not run the pass. They read a proposal to decide whether to accept it, and later read it again
to build what the acceptance minted. They think in interfaces, types, call sites, and data. Give them those, quoted from
the code, and let a sentence carry only what no code block can: a reason, an ordering, a failure mode, a cost.

## A finding's summary

One instance, stated so the reader can check it without opening the scope:

1. The standard it breaks, by the id or path the axis's criteria name.
2. The evidence at the locus, quoted or paraphrased in one line.
3. What conforming would look like, when that is not obvious from the standard.

> `bzh:one-owner` — `RunnerLease.ttl` is defined here and again in `runner/config.py:41` with a different default (90 s
> vs 120 s); the runner's copy should read the hub's.

Not: "Some duplication of lease configuration was observed that may cause inconsistency."

## A proposal's title

Write the title as the issue: what is wrong, stated concretely enough that the reader knows why it matters before
opening the body. *"Lease TTL has two owners with different defaults"*. The reader judges the title before anything is
built, a design weighs more than one fix, and a passed proposal leaves the issue standing, so the title names the issue
and the body carries the fix.

The one exception is a change at named sites, where the fix is single and obvious and the premise already states the
issue. Title it as the change, in the imperative: *"Remove change-history narration from 17 billing docstrings"*.

An idea about the product names the person's problem: *"Operators cannot see why a chunk is parked without opening its
trace"*.

A rule id, a class, or a finding count is not a title. Keep it under about 80 characters.

## A proposal's body

The body is markdown. Lead with a one-line premise: the problem, at the sites, against the standard. Then use the shape
that fits the proposal's size. Use only the sections that carry something, and keep their order.

### A change at named sites

The change stays inside the code the findings point at, and no interface other code calls changes shape.

```markdown
**Premise:** <one sentence: what is wrong, where, against which standard>

## Sites

- `path/to/file.py:41` — <the offending line or symbol, quoted briefly>

## Change

<before / after, as code, 5 to 20 lines>

## Cost

<files touched; what could break; how the change is verified>
```

### A design

The change alters an interface, a type, a schema, or a component boundary that other code depends on, or it chooses
between more than one way to do the work. Show the design itself so the reader can judge it without asking for it.

```markdown
**Premise:** <one sentence: what goes wrong because of the issue the title names>

**Decision:** <the one question the reader is being asked to settle>

## Today

<the current interfaces and types, quoted with file:line>

## Proposed

<the new types, interfaces, and signatures, as code; before -> after where a signature changes>

## Flow

<only when behaviour crosses components: a numbered sequence or a diagram, including the failure path>

## Options

| Option | Scope | Cost | Leaves out |
| ------ | ----- | ---- | ---------- |

## Recommendation

<one option, and the reason in one or two sentences>

## Open questions

<what the reader must answer that the run could not>
```

### An idea about the product

The proposal is about what the product could become, not about a rule broken in the code. Write it for whoever holds the
product's intent. Start with the person whose work is hard and what that costs them, then the response, in connected
prose. A short `## Shape` with the concept's types or surfaces may follow when one would make it concrete.

## Plain statements

- Say what the system does, under which condition, with numbers and units where there are any.
- Use the common term: *retry*, *timeout*, *cache*, *queue*, *endpoint*, not a coined one.
- Say what you are unsure of where it applies, once and plainly. Say when the proposal is speculative, or when you doubt
  it is worth the cost.

## Worked examples

A finding in this voice:

### FindingCandidate

```json
{
  "ref": "F1",
  "class": "duplicated-default",
  "locus": "src/runner/config.py:41",
  "summary": "Breaks one-owner-per-fact: `lease_ttl` defaults to 120 s here and to 90 s on the hub's `RunnerLease.ttl`. The runner should read the hub's value.",
  "introduced": "a1b2c3d"
}
```

A design-sized proposal in this voice:

### GardenProposalCandidate

````json
{
  "ref": "P1",
  "class": "escalate",
  "title": "Lease TTL has two owners with different defaults",
  "body": "**Premise:** the hub expires a lease at 90 s while the runner renews on a 120 s TTL, so a runner can renew a lease the hub has already expired.\n\n**Decision:** does the hub hand the TTL to the runner on claim, or does the runner fetch it from config?\n\n## Today\n```python\n# hub/lease.py:12\nclass RunnerLease:\n    ttl: timedelta = timedelta(seconds=90)\n\n# runner/config.py:41\nlease_ttl: int = 120\n```\n\n## Proposed\n```python\n# wire/claim.py\nclass ClaimGrant(BaseModel):\n    chunk_id: str\n    lease_ttl_s: int  # new: the hub's RunnerLease.ttl\n```\nThe runner drops `lease_ttl` and renews at `lease_ttl_s / 3`.\n\n## Options\n| Option | Scope | Cost | Leaves out |\n| --- | --- | --- | --- |\n| A: carry it on the claim | wire + runner | 1 field, 2 files | nothing |\n| B: runner reads hub config | runner | 1 new GET call | a TTL change mid-lease |\n\n## Recommendation\nA: the TTL arrives with the lease it governs, so it cannot go stale.",
  "findings": ["F1"]
}
````

## Tells to rewrite

These read fluently and say little. Rewrite them when you find them in your own text:

- Filler verbs: *ensure*, *leverage*, *facilitate*, *enable*, where the sentence should say what the system does.
- Sales adjectives: *robust*, *seamless*, *scalable*, *comprehensive*.
- Triads chosen for rhythm: "reliable, performant, and secure".
- Layered hedging: "should typically aim to", where a bound or a plain doubt would do.
- An opener that restates the title, and meta lines: "This proposal describes…", "Note that…".
- Em-dash chains stacking asides into one sentence.
- An inventory of findings where the case for acting should be.
- A rule id or internal label used in place of saying what is wrong.

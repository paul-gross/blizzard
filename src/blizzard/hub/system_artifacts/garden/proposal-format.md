# garden/proposal-format

The shape a garden routine's run submits for a proposal. This is blizzard's own format, published the same way
`garden/finding-format` is — a garden graph must never carry its own copy of it, since the delivery script validates a
submission against exactly this shape.

A proposal is a proposed response — to the findings behind it, for a graph that has any. It has no id until delivery
mints one; `ref` names it only within its own submission, the same way a finding candidate's `ref` does.

## The proposal a run submits

- `ref` — a local reference, stable only within this submission.
- `class` — the deployment's own taxonomy for a kind of response, exactly as a finding's `class` is its taxonomy for a
  kind of weed. blizzard stores it, indexes it, groups by it, and never interprets it — a deployment declares its own
  vocabulary of proposal classes and settles what any of them mean.
- `title` — a short label for the proposal.
- `body` — markdown: the case for it, in enough detail that a person can decide without re-reading the findings behind
  it.
- `findings` — the findings this proposal answers. May be empty — the hub enforces no minimum; whether a submitting
  graph's own proposals must cite one is that graph's own propose prompt to decide, never the hub's. Each entry given is
  either a `fin_` id already live on this routine, or the submission-local `ref` an `add` op in a delta delivered by
  this same call carries — resolved against the id the hub mints for it at delivery, so a proposal may answer a finding
  this very run opened.

How `title` and `body` read is `garden/voice`'s to say, unless the deployment's own agent-facing context states a voice
for garden proposals, which then replaces it whole.

### GardenProposalCandidate

```json
{
  "ref": "P1",
  "class": "fix-the-source",
  "title": "Billing docstrings narrate change history, and no standard says what they state",
  "body": "**Premise:** 17 modules under `src/billing/` open with a docstring that narrates their change history instead of stating the contract, and no standard says which is right.\n\n## Sites\n- `src/billing/invoice.py:1`\n- `src/billing/ledger.py:1`\n\n## Change\nAdd `docs/standards/docstrings.md`: a module docstring states what the module guarantees; its history stays in git.\n\n## Cost\nOne new document. The axis's criteria point at it, so later runs judge each docstring against it.",
  "findings": ["fin_01JKQ8Z3M4N5P6R7S8T9V0W1X2", "fin_01JKQ8Z3M4N5P6R7S8T9V0W1X3"]
}
```

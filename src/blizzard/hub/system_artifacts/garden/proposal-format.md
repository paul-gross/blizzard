# garden/proposal-format

The shape a garden routine's run submits for a proposal. This is blizzard's own format,
published the same way `garden/finding-format` is — a garden graph must never carry its
own copy of it, since the delivery script validates a submission against exactly this
shape.

A proposal is a proposed response to one or more findings. It has no id until delivery
mints one; `ref` names it only within its own submission, the same way a finding
candidate's `ref` does.

- `ref` — a local reference, stable only within this submission.
- `class` — the deployment's own taxonomy for a kind of response, exactly as a finding's
  `class` is its taxonomy for a kind of weed. blizzard stores it, indexes it, groups by
  it, and never interprets it — a deployment declares its own vocabulary of proposal
  classes and settles what any of them mean.
- `title` — a short label for the response.
- `body` — the case for it, in enough detail that a person can decide without re-reading
  the findings behind it.
- `findings` — the ids of the findings this proposal answers. Required and non-empty: a
  proposal with nothing behind it is an opinion the run was not asked for.

### GardenProposalCandidate

```json
{
  "ref": "P1",
  "class": "fix-the-source",
  "title": "Author a docstring standard covering change-history narration",
  "body": "Seventeen modules narrate their own change history in the docstring rather than stating the contract; a written standard gives future edits something to check against instead of re-litigating the question per file.",
  "findings": ["fin_01JKQ8Z3M4N5P6R7S8T9V0W1X2", "fin_01JKQ8Z3M4N5P6R7S8T9V0W1X3"]
}
```

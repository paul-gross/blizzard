# The packaged ideation graph

Conventions for the `prompts/` tree are owned by the
[prompt-authoring README](../advanced-development-workflow/README.md) — read it before adding or editing a node prompt.

This graph carries no frozen plan artifact to correct against, unlike `garden-routine`'s — it was authored directly as a
packaged graph, so it has no corrections section of its own.

`survey` publishes the empty `survey-delta` skeleton. `deliver-clean` reads it for empty and nothing-new runs; `propose`
alone publishes the corrected `delta` read by `deliver`. This keeps each delivery input's producer unambiguous. A failed
clean delivery returns to survey with a bounded recovery addendum.

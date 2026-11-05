---
kind: changed
surfaces: [http-api, cli, config]
---

A routine now has a `slug` beside its `name`. The slug is the routine's lineage and handle: immutable, unique within its
project (a retired routine keeps its slug), and what its findings, baselines, runs, proposals and change log are keyed
by. The `name` narrows from handle to a free, editable display name. A create or a document entry states a `slug`, a
`name`, or both, and an existing routine's slug is the name it already carried. `routine create` takes the slug as its
first argument and a display name with `--name`; `routine edit` gains `--name`; the other routine verbs, and the
`routine` query of the trend and proposal-counts reads, take the slug. A client that keys a routine by `name` stops
finding it once its display name is edited.
